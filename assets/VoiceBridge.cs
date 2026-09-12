// VoiceBridge.cs — DSH 语音桥核心（C#，由 bridge.ps1 通过 Add-Type 编译加载）
//
// 功能：
//   1) 全局监听耳机媒体键（播放/暂停等）或回退键盘热键 -> 触发录音
//   2) winmm waveIn 从默认麦克风（耳机麦）录音，16kHz 单声道 16bit
//   3) 音量检测（RMS）：开始说话后静音超过 hangover 自动停止；没说话 4 秒自动放弃
//   4) 录音中再按一次触发键 = 立即发送
//   5) 输出标准 WAV 文件，供 faster-whisper 转写
//
// 由 PowerShell 驱动：设置静态配置 -> Start() -> 循环 WaitForNextCapture()。

using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;

public static class VoiceBridge
{
    // ---------------- winmm (录音) ----------------
    private const uint WAVE_MAPPER = 0xFFFFFFFF; // WAVE_MAPPER = -1
    private const uint CALLBACK_FUNCTION = 0x00030000;
    private const uint MM_WIM_DATA = 0x03C0;
    private const ushort WAVE_FORMAT_PCM = 1;

    [StructLayout(LayoutKind.Sequential)]
    private struct WAVEFORMATEX
    {
        public ushort wFormatTag;
        public ushort nChannels;
        public uint nSamplesPerSec;
        public uint nAvgBytesPerSec;
        public ushort nBlockAlign;
        public ushort wBitsPerSample;
        public ushort cbSize;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct WAVEHDR
    {
        public IntPtr lpData;
        public uint dwBufferLength;
        public uint dwBytesRecorded;
        public IntPtr dwUser;
        public uint dwFlags;
        public uint dwLoops;
        public IntPtr lpNext;
        public IntPtr reserved;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi)]
    private struct WAVEINCAPS
    {
        public ushort wMid;
        public ushort wPid;
        public uint vDriverVersion;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)]
        public string szPname;
        public uint dwFormats;
        public ushort wChannels;
        public ushort wReserved1;
    }

    private delegate void WaveInProc(IntPtr hwi, uint uMsg, IntPtr dwInstance, IntPtr dwParam1, IntPtr dwParam2);
    public delegate bool CommitCheck();

    [DllImport("winmm.dll")]
    private static extern uint waveInGetNumDevs();
    [DllImport("winmm.dll", CharSet = CharSet.Ansi)]
    private static extern uint waveInGetDevCaps(uint uDeviceID, out WAVEINCAPS pCaps, uint cbCaps);
    [DllImport("winmm.dll")]
    private static extern uint waveInOpen(out IntPtr phwi, uint uDeviceID, ref WAVEFORMATEX pwfx,
        WaveInProc dwCallback, IntPtr dwInstance, uint dwFlags);
    [DllImport("winmm.dll")]
    private static extern uint waveInPrepareHeader(IntPtr hwi, ref WAVEHDR pwh, uint cbwh);
    [DllImport("winmm.dll")]
    private static extern uint waveInAddBuffer(IntPtr hwi, ref WAVEHDR pwh, uint cbwh);
    [DllImport("winmm.dll")]
    private static extern uint waveInStart(IntPtr hwi);
    [DllImport("winmm.dll")]
    private static extern uint waveInStop(IntPtr hwi);
    [DllImport("winmm.dll")]
    private static extern uint waveInReset(IntPtr hwi);
    [DllImport("winmm.dll")]
    private static extern uint waveInUnprepareHeader(IntPtr hwi, ref WAVEHDR pwh, uint cbwh);
    [DllImport("winmm.dll")]
    private static extern uint waveInClose(IntPtr hwi);

    // ---------------- user32 / kernel32 (按键钩子) ----------------
    private const int WH_KEYBOARD_LL = 13;
    private const int WM_KEYDOWN = 0x0100;
    private const int WM_HOTKEY = 0x0312;
    private const uint WM_QUIT = 0x0012;

    public const uint VK_MEDIA_PLAY_PAUSE = 0xB3;
    public const uint VK_MEDIA_NEXT_TRACK = 0xB0;
    public const uint VK_MEDIA_PREV_TRACK = 0xB1;

    private delegate IntPtr LowLevelKeyboardProc(int nCode, IntPtr wParam, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential)]
    private struct KBDLLHOOKSTRUCT
    {
        public uint vkCode;
        public uint scanCode;
        public uint flags;
        public uint time;
        public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MSG
    {
        public IntPtr hwnd;
        public uint message;
        public IntPtr wParam;
        public IntPtr lParam;
        public uint time;
        public int ptX;
        public int ptY;
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr SetWindowsHookEx(int idHook, LowLevelKeyboardProc lpfn, IntPtr hMod, uint dwThreadId);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool UnhookWindowsHookEx(IntPtr hhk);
    [DllImport("user32.dll")]
    private static extern IntPtr CallNextHookEx(IntPtr hhk, int nCode, IntPtr wParam, IntPtr lParam);
    [DllImport("kernel32.dll", CharSet = CharSet.Ansi)]
    private static extern IntPtr GetModuleHandle(string lpModuleName);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool UnregisterHotKey(IntPtr hWnd, int id);
    [DllImport("user32.dll")]
    private static extern bool GetMessage(out MSG lpMsg, IntPtr hWnd, uint wMsgFilterMin, uint wMsgFilterMax);
    [DllImport("user32.dll")]
    private static extern bool TranslateMessage(ref MSG lpMsg);
    [DllImport("user32.dll")]
    private static extern IntPtr DispatchMessage(ref MSG lpMsg);
    [DllImport("user32.dll")]
    private static extern bool PostThreadMessage(uint idThread, uint Msg, IntPtr wParam, IntPtr lParam);

    // ---------------- 配置（PowerShell 启动前设置） ----------------
    public static uint[] TriggerVks = new uint[] { VK_MEDIA_PLAY_PAUSE };
    public static bool ConsumeMediaKey = true;      // 吞掉触发键（不转发给播放器）
    public static int InputDeviceId = -1;           // -1 = 系统默认输入设备（耳机麦需设为默认）
    public static int SampleRate = 16000;
    public static double SilenceThreshold = 0.012;  // RMS 低于此值视为静音（可调）
    public static int SilenceHangoverMs = 1100;     // 静音持续这么久后自动停止
    public static int MaxRecordMs = 30000;          // 最长录音 30 秒
    public static int NoSpeechAbortMs = 4000;       // 开始后这么久没说话就放弃
    public static string OutputDir = "";            // WAV 输出目录（空 = 临时目录）

    // ---------------- 运行时状态 ----------------
    private static readonly object Gate = new object();
    private static readonly Queue<string> Captures = new Queue<string>();
    private static readonly ManualResetEventSlim Signal = new ManualResetEventSlim(false);
    private static LowLevelKeyboardProc _hookProc;
    private static IntPtr _hook;
    private static Thread _pumpThread;
    private static volatile bool _running;
    private static volatile bool _recording;
    private static volatile bool _commitNow;
    private static readonly HashSet<uint> _triggerSet = new HashSet<uint>();

    public static string LogPath = "";   // 日志文件路径（空 = 不写文件）

    // ---------------- 对外 API ----------------

    public static void Log(string line)
    {
        string msg = DateTime.Now.ToString("HH:mm:ss.fff") + " " + line;
        try
        {
            if (!string.IsNullOrEmpty(LogPath))
                File.AppendAllText(LogPath, msg + Environment.NewLine);
        }
        catch { }
        try { Console.WriteLine(msg); } catch { }
    }

    /// <summary>列出所有输入设备（索引: 名称）。</summary>
    public static string[] ListInputDevices()
    {
        var names = new List<string>();
        uint n = waveInGetNumDevs();
        for (uint i = 0; i < n; i++)
        {
            WAVEINCAPS caps;
            if (waveInGetDevCaps(i, out caps, (uint)Marshal.SizeOf(typeof(WAVEINCAPS))) == 0)
                names.Add(i + ": " + caps.szPname);
        }
        return names.ToArray();
    }

    /// <summary>系统默认输入设备名称（即录音会用到的设备）。</summary>
    public static string GetDefaultInputDeviceName()
    {
        WAVEINCAPS caps;
        if (waveInGetDevCaps(WAVE_MAPPER, out caps, (uint)Marshal.SizeOf(typeof(WAVEINCAPS))) == 0)
            return caps.szPname;
        return "(无法读取默认输入设备)";
    }

    /// <summary>启动监听：安装钩子 + 消息泵。只能调用一次。</summary>
    public static void Start()
    {
        lock (Gate)
        {
            if (_running) return;
            _running = true;
            _triggerSet.Clear();
            if (TriggerVks != null)
                foreach (uint vk in TriggerVks) _triggerSet.Add(vk);
            _pumpThread = new Thread(Pump) { IsBackground = true, Name = "voicebridge-pump" };
            _pumpThread.Start();
        }
    }

    /// <summary>停止监听并卸载钩子。</summary>
    public static void Stop()
    {
        lock (Gate)
        {
            if (!_running) return;
            _running = false;
        }
        if (_pumpThread != null)
        {
            try { PostThreadMessage((uint)_pumpThread.ManagedThreadId, WM_QUIT, IntPtr.Zero, IntPtr.Zero); } catch { }
            _pumpThread.Join(2000);
        }
    }

    /// <summary>阻塞等待下一次录音完成，返回 WAV 路径；超时返回 null。</summary>
    public static string WaitForNextCapture(int timeoutMs)
    {
        while (true)
        {
            lock (Gate)
            {
                if (Captures.Count > 0)
                {
                    string p = Captures.Dequeue();
                    if (Captures.Count == 0) Signal.Reset();
                    return p;
                }
            }
            if (!Signal.Wait(timeoutMs)) return null;
        }
    }

    public static bool IsRecording { get { return _recording; } }

    /// <summary>指令模式开关。false = 忽略耳机键触发（会议录音期间由桥置为 false）。</summary>
    public static bool CommandEnabled = true;

    /// <summary>测试辅助：录固定时长的声音（不做静音检测），返回 WAV 路径。</summary>
    public static string RecordSample(int deviceId, int ms)
    {
        string dir = string.IsNullOrEmpty(OutputDir) ? Path.GetTempPath() : OutputDir;
        return Recorder.Capture(deviceId, SampleRate, -1, 0, ms, 0, dir, () => false);
    }

    // ---------------- 内部实现 ----------------

    private static void Pump()
    {
        _hookProc = HookProc;
        _hook = SetWindowsHookEx(WH_KEYBOARD_LL, _hookProc, GetModuleHandle(null), 0);
        if (_hook == IntPtr.Zero)
            Log("SetWindowsHookEx 失败: " + Marshal.GetLastWin32Error());
        else
            Log("按键钩子已安装 (WH_KEYBOARD_LL)");
        foreach (HotkeySpec hk in Hotkeys)
        {
            if (!RegisterHotKey(IntPtr.Zero, hk.Id, hk.Mods, hk.Vk))
                Log("RegisterHotKey 失败 (mods=0x" + hk.Mods.ToString("X") + ", vk=0x" + hk.Vk.ToString("X2") + "): " + Marshal.GetLastWin32Error());
            else
                Log("热键已注册: mods=0x" + hk.Mods.ToString("X") + " vk=0x" + hk.Vk.ToString("X2"));
        }
        MSG msg;
        while (_running && GetMessage(out msg, IntPtr.Zero, 0, 0))
        {
            if (msg.message == WM_HOTKEY && HotkeyIdSet.Contains(msg.wParam.ToInt32()))
                Trigger();
            TranslateMessage(ref msg);
            DispatchMessage(ref msg);
        }
        if (_hook != IntPtr.Zero) UnhookWindowsHookEx(_hook);
        foreach (HotkeySpec hk in Hotkeys)
            UnregisterHotKey(IntPtr.Zero, hk.Id);
        _hook = IntPtr.Zero;
        Log("消息泵已退出");
    }

    /// <summary>一个键盘组合键热键（RegisterHotKey）。</summary>
    public sealed class HotkeySpec
    {
        public int Id;
        public uint Mods;
        public uint Vk;
        public HotkeySpec(int id, uint mods, uint vk) { Id = id; Mods = mods; Vk = vk; }
    }

    private static readonly List<HotkeySpec> Hotkeys = new List<HotkeySpec>();
    private static readonly HashSet<int> HotkeyIdSet = new HashSet<int>();
    private static int _nextHotkeyId = 0x9100;

    /// <summary>添加一个键盘组合键热键（mods 见 RegisterHotKey 的 MOD_* 常量，vk 为虚拟键码）。</summary>
    public static void AddHotkey(uint mods, uint vk)
    {
        lock (Gate)
        {
            if (mods == 0 || vk == 0) return;
            HotkeySpec spec = new HotkeySpec(_nextHotkeyId++, mods, vk);
            Hotkeys.Add(spec);
            HotkeyIdSet.Add(spec.Id);
        }
    }

    private static IntPtr HookProc(int nCode, IntPtr wParam, IntPtr lParam)
    {
        if (nCode >= 0 && wParam.ToInt64() == WM_KEYDOWN)
        {
            KBDLLHOOKSTRUCT info = (KBDLLHOOKSTRUCT)Marshal.PtrToStructure(lParam, typeof(KBDLLHOOKSTRUCT));
            if (LogAllKeys)
            {
                Log("KEY 0x" + info.vkCode.ToString("X2") + " " + KeyName(info.vkCode));
            }
            if (SwallowAllKeys) return (IntPtr)1;   // 测试模式：拦截所有按键
            if (_triggerSet.Contains(info.vkCode))
            {
                if (CommandEnabled) Trigger();   // 指令模式关闭时忽略触发（会议录音期间）
                if (ConsumeMediaKey) return (IntPtr)1; // 吞掉，避免同时触发播放器
            }
        }
        return CallNextHookEx(_hook, nCode, wParam, lParam);
    }

    /// <summary>诊断模式：把所有按键事件写入日志（用于识别麦克风/耳机的特殊按键）。</summary>
    public static bool LogAllKeys = false;

    /// <summary>测试模式：拦截（吞掉）所有按键事件，配合 LogAllKeys 用于按键捕获测试。</summary>
    public static bool SwallowAllKeys = false;

    private static string KeyName(uint vk)
    {
        switch (vk)
        {
            case 0xAD: return "VolumeMute";
            case 0xAE: return "VolumeDown";
            case 0xAF: return "VolumeUp";
            case 0xB0: return "MediaNext";
            case 0xB1: return "MediaPrev";
            case 0xB2: return "MediaStop";
            case 0xB3: return "PlayPause";
            case 0xE2: return "Play";
            case 0xE3: return "Pause";
            case 0xE9: return "MicMute";
            case 0xEB: return "PlayPause2";
            case 0xA0: return "LShift";
            case 0xA4: return "LAlt";
            case 0xA2: return "LCtrl";
            default: return "VK" + vk.ToString("X2");
        }
    }

    private static long _lastTriggerTicks = 0;

    private static void Trigger()
    {
        // 防自动连发：音量键/部分设备按住会连续触发多次，500ms 内合并为一次
        long now = Environment.TickCount;
        if (now - _lastTriggerTicks < 500) return;
        _lastTriggerTicks = now;
        lock (Gate)
        {
            if (_recording) { _commitNow = true; return; }  // 再按一次 = 立即发送
        }
        Thread t = new Thread(() =>
        {
            try { RunCapture(); }
            catch (Exception ex) { Log("录音异常: " + ex); }
        }) { IsBackground = true };
        t.Start();
    }

    private static void RunCapture()
    {
        lock (Gate) { _recording = true; _commitNow = false; }
        string path = null;
        try
        {
            string dir = string.IsNullOrEmpty(OutputDir) ? Path.GetTempPath() : OutputDir;
            path = Recorder.Capture(InputDeviceId, SampleRate, SilenceThreshold, SilenceHangoverMs,
                MaxRecordMs, NoSpeechAbortMs, dir, () => _commitNow);
        }
        catch (Exception ex)
        {
            Log("录音失败: " + ex.Message);
        }
        finally
        {
            lock (Gate) { _recording = false; _commitNow = false; }
        }
        if (path != null)
        {
            Log("录音完成: " + Path.GetFileName(path));
            lock (Gate) { Captures.Enqueue(path); }
            Signal.Set();
        }
        else
        {
            Log("录音放弃（没听到说话）");
        }
    }

    private sealed class Recorder
    {
        private readonly int _deviceId;
        private readonly int _sampleRate;
        private readonly double _threshold;      // < 0 表示纯时长模式
        private readonly int _hangoverMs;
        private readonly int _maxMs;
        private readonly int _noSpeechMs;
        private readonly string _outDir;
        private readonly CommitCheck _commitNow;

        private IntPtr _hwi;
        private WAVEHDR[] _hdrs;
        private GCHandle _hdrsPin;
        private readonly List<GCHandle> _bufPins = new List<GCHandle>();
        private byte[][] _bufs;
        private readonly MemoryStream _pcm = new MemoryStream();
        private readonly object _vadGate = new object();
        private WaveInProc _cb;
        private volatile bool _done;
        private long _recStart;
        private long _lastSpeech = -1;
        private string _result;

        private Recorder(int deviceId, int sampleRate, double threshold, int hangoverMs, int maxMs,
            int noSpeechMs, string outDir, CommitCheck commitNow)
        {
            _deviceId = deviceId;
            _sampleRate = sampleRate;
            _threshold = threshold;
            _hangoverMs = hangoverMs;
            _maxMs = maxMs;
            _noSpeechMs = noSpeechMs;
            _outDir = outDir;
            _commitNow = commitNow;
        }

        public static string Capture(int deviceId, int sampleRate, double threshold, int hangoverMs,
            int maxMs, int noSpeechMs, string outDir, CommitCheck commitNow)
        {
            Recorder r = new Recorder(deviceId, sampleRate, threshold, hangoverMs, maxMs, noSpeechMs, outDir, commitNow);
            return r.Run();
        }

        private string Run()
        {
            int chunkBytes = _sampleRate * 2 / 10; // 100ms @ 16bit mono
            int bufCount = 8;
            _bufs = new byte[bufCount][];
            _hdrs = new WAVEHDR[bufCount];
            for (int i = 0; i < bufCount; i++)
            {
                _bufs[i] = new byte[chunkBytes];
                _bufPins.Add(GCHandle.Alloc(_bufs[i], GCHandleType.Pinned));
                _hdrs[i].lpData = Marshal.UnsafeAddrOfPinnedArrayElement(_bufs[i], 0);
                _hdrs[i].dwBufferLength = (uint)chunkBytes;
                _hdrs[i].dwUser = new IntPtr(i); // 用 dwUser 携带缓冲索引
            }
            _hdrsPin = GCHandle.Alloc(_hdrs, GCHandleType.Pinned);

            WAVEFORMATEX fmt = new WAVEFORMATEX();
            fmt.wFormatTag = WAVE_FORMAT_PCM;
            fmt.nChannels = 1;
            fmt.nSamplesPerSec = (uint)_sampleRate;
            fmt.wBitsPerSample = 16;
            fmt.nBlockAlign = 2;
            fmt.nAvgBytesPerSec = (uint)(_sampleRate * 2);
            fmt.cbSize = 0;

            _cb = OnData;
            uint err = waveInOpen(out _hwi, (uint)_deviceId, ref fmt, _cb, IntPtr.Zero, CALLBACK_FUNCTION);
            if (err != 0)
            {
                Cleanup();
                throw new Exception("waveInOpen 失败 (设备 " + _deviceId + "): " + err + "。请检查默认麦克风设置。");
            }
            try
            {
                uint hdrSize = (uint)Marshal.SizeOf(typeof(WAVEHDR));
                for (int i = 0; i < _hdrs.Length; i++)
                {
                    err = waveInPrepareHeader(_hwi, ref _hdrs[i], hdrSize);
                    if (err != 0) throw new Exception("waveInPrepareHeader 失败: " + err);
                    err = waveInAddBuffer(_hwi, ref _hdrs[i], hdrSize);
                    if (err != 0) throw new Exception("waveInAddBuffer 失败: " + err);
                }
                err = waveInStart(_hwi);
                if (err != 0) throw new Exception("waveInStart 失败: " + err);
                _recStart = Environment.TickCount;

                while (!_done)
                {
                    Thread.Sleep(40);
                    if (_commitNow()) { _done = true; break; }
                    if (Environment.TickCount - _recStart > (long)_maxMs * 2 + 5000) { _done = true; break; } // 保险超时
                }
            }
            finally
            {
                StopAndSave();
            }
            return _result;
        }

        private void OnData(IntPtr hwi, uint uMsg, IntPtr dwInstance, IntPtr dwParam1, IntPtr dwParam2)
        {
            if (uMsg != MM_WIM_DATA || _done) return;
            try
            {
                // 用指针线性匹配定位缓冲索引（避免除法指针数学的隐患）
                int index = -1;
                long p = dwParam1.ToInt64();
                for (int i = 0; i < _hdrs.Length; i++)
                {
                    if (Marshal.UnsafeAddrOfPinnedArrayElement(_hdrs, i).ToInt64() == p) { index = i; break; }
                }
                if (index < 0 || index >= _hdrs.Length) return;
                WAVEHDR hdr = _hdrs[index];
                uint bytes = hdr.dwBytesRecorded;
                if (bytes > 0 && hdr.lpData != IntPtr.Zero)
                {
                    byte[] chunk = new byte[bytes];
                    Marshal.Copy(hdr.lpData, chunk, 0, (int)bytes);
                    _pcm.Write(chunk, 0, chunk.Length);
                    if (_threshold >= 0)
                    {
                        double rms = Rms(chunk);
                        long now = Environment.TickCount;
                        lock (_vadGate)
                        {
                            if (rms >= _threshold)
                            {
                                _lastSpeech = now;
                            }
                            else if (_lastSpeech >= 0 && now - _lastSpeech >= _hangoverMs)
                            {
                                _done = true;   // 说完 + 静音 hangover -> 停止
                            }
                            else if (_lastSpeech < 0 && now - _recStart >= _noSpeechMs)
                            {
                                _done = true;   // 一直没说话 -> 放弃
                            }
                            if (!_done && now - _recStart >= _maxMs)
                                _done = true;   // 最长时长硬上限
                        }
                    }
                    else
                    {
                        // 纯时长模式（测试用）
                        if (Environment.TickCount - _recStart >= _maxMs) _done = true;
                    }
                }
                if (_done) return;  // 不再放回缓冲区
                ReAdd(index);
            }
            catch (Exception ex)
            {
                Log("OnData 回调异常: " + ex.Message);
            }
        }

        private void ReAdd(int index)
        {
            if (_done) return;
            uint hdrSize = (uint)Marshal.SizeOf(typeof(WAVEHDR));
            _hdrs[index].dwBytesRecorded = 0;
            _hdrs[index].dwFlags = 0;
            uint e1 = waveInUnprepareHeader(_hwi, ref _hdrs[index], hdrSize);
            uint e2 = waveInPrepareHeader(_hwi, ref _hdrs[index], hdrSize);
            uint e3 = waveInAddBuffer(_hwi, ref _hdrs[index], hdrSize);
            if (e1 != 0 || e2 != 0 || e3 != 0)
            {
                Log("ReAdd index=" + index + " unprepare=" + e1 + " prepare=" + e2 + " add=" + e3 + " hdrSize=" + hdrSize);
                if (e2 != 0 || e3 != 0) _done = true;
            }
        }

        private static double Rms(byte[] chunk)
        {
            int n = chunk.Length / 2;
            if (n <= 0) return 0;
            double sum = 0;
            for (int i = 0; i < n; i++)
            {
                short s = (short)(chunk[i * 2] | (chunk[i * 2 + 1] << 8));
                double v = s / 32768.0;
                sum += v * v;
            }
            return Math.Sqrt(sum / n);
        }

        private void StopAndSave()
        {
            try { waveInStop(_hwi); } catch { }
            try { waveInReset(_hwi); } catch { }
            try
            {
                uint hdrSize = (uint)Marshal.SizeOf(typeof(WAVEHDR));
                for (int i = 0; i < _hdrs.Length; i++)
                {
                    try { waveInUnprepareHeader(_hwi, ref _hdrs[i], hdrSize); } catch { }
                }
                waveInClose(_hwi);
            }
            catch { }
            try
            {
                bool spoke = _lastSpeech >= 0 || _threshold < 0;
                if (spoke && _pcm.Length > 0)
                {
                    string name = "voice-" + DateTime.Now.ToString("yyyyMMdd-HHmmss-fff") + ".wav";
                    string path = Path.Combine(_outDir, name);
                    WriteWav(path, _pcm.ToArray(), _sampleRate);
                    _result = path;
                }
            }
            catch (Exception ex)
            {
                Log("保存 WAV 失败: " + ex.Message);
            }
            Cleanup();
        }

        private void Cleanup()
        {
            try
            {
                if (_hdrsPin.IsAllocated) _hdrsPin.Free();
                foreach (GCHandle h in _bufPins) if (h.IsAllocated) h.Free();
                _bufPins.Clear();
            }
            catch { }
        }

        private static void WriteWav(string path, byte[] pcm, int sampleRate)
        {
            using (FileStream fs = new FileStream(path, FileMode.Create, FileAccess.Write))
            using (BinaryWriter w = new BinaryWriter(fs))
            {
                int dataLen = pcm.Length;
                w.Write(System.Text.Encoding.ASCII.GetBytes("RIFF"));
                w.Write(36 + dataLen);
                w.Write(System.Text.Encoding.ASCII.GetBytes("WAVE"));
                w.Write(System.Text.Encoding.ASCII.GetBytes("fmt "));
                w.Write(16);
                w.Write((ushort)WAVE_FORMAT_PCM);
                w.Write((ushort)1);            // mono
                w.Write((uint)sampleRate);
                w.Write((uint)(sampleRate * 2));
                w.Write((ushort)2);            // block align
                w.Write((ushort)16);           // bits
                w.Write(System.Text.Encoding.ASCII.GetBytes("data"));
                w.Write(dataLen);
                w.Write(pcm);
            }
        }
    }
}
