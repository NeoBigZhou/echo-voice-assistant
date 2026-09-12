// ECHO PWA Service Worker
// 目的：满足 PWA 可安装要求（独立窗口）。不缓存任何内容——
// 所有请求走网络，与后端 no-store 策略保持一致（面板始终加载最新版）。
self.addEventListener("install", (e) => {
  self.skipWaiting();
});
self.addEventListener("activate", (e) => {
  e.waitUntil(self.clients.claim());
});
// 不拦截 fetch：默认网络行为，永不缓存。
