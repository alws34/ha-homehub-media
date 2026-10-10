/* HomeHub Media card: remote, ambience and library for the homehub_media integration.
 * Vanilla web component, no build step, no third-party cards.
 * Backend contracts only: websocket homehub_media/subscribe (snapshot), homehub_media/catalog,
 * homehub_media/library, and hass.callService("homehub_media", ...).
 * Every dynamic string goes through esc(); image URLs must be same-origin (safeUrl). */

const TAG = "homehub-media-card";
const DOMAIN = "homehub_media";
const DRAWER_KEY = "homehub-media-card:drawer";
const PAD_KEY = "homehub-media-card:pad";  // "touchpad" or "dpad", per browser
const SWIPE_STEP = 48;  // px of swipe per arrow key on a TV without pointer input
const TABS = {
  remote: ["Remote", "mdi:remote-tv"],
  ambience: ["Ambience", "mdi:weather-night"],
  library: ["Library", "mdi:filmstrip-box-multiple"],
};
// short label, accessible label, icon
const TARGETS = {
  tv: ["TV", "TV", "mdi:television"],
  box: ["Box", "Streaming box", "mdi:set-top-box"],
  pc: ["PC", "Media PC", "mdi:desktop-tower-monitor"],
};
// app tile [background, text]: brand colours, contrast-checked against the text colour
const BRANDS = {
  netflix: ["#000", "#f5141f"],  // a touch lighter than brand red for 5:1 contrast
  youtube: ["#e62117", "#fff"],
  prime_video: ["#0f171e", "#00a8e1"],
  disney_plus: ["linear-gradient(135deg,#0b1647,#1a3a9e)", "#fff"],
  apple_tv: ["#1c1c1e", "#fff"],
  max: ["#002be7", "#fff"],
  plex: ["#1f1f1f", "#e5a00d"],
  jellyfin: ["linear-gradient(135deg,#7b3fa0,#0b6f9e)", "#fff"],
  spotify: ["#1db954", "#0b0b0b"],
  twitch: ["#9146ff", "#fff"],
  paramount_plus: ["#0064ff", "#fff"],
};
// app logos: Simple Icons 13.21.0 path data (CC0; trademarks belong to their owners), drawn in the tile's text colour.
// Disney+ and Max are not in Simple Icons: they get a wordmark; the browser a generic icon.
const LOGOS = {
  netflix: "M5.398 0v.006c3.028 8.556 5.37 15.175 8.348 23.596 2.344.058 4.85.398 4.854.398-2.8-7.924-5.923-16.747-8.487-24zm8.489 0v9.63L18.6 22.951c-.043-7.86-.004-15.913.002-22.95zM5.398 1.05V24c1.873-.225 2.81-.312 4.715-.398v-9.22z",
  youtube: "M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z",
  prime_video: "M0 9.508c0-.043.01-.073.028-.09.018-.017.047-.025.086-.025h.329c.07 0 .112.034.127.101l.032.119c.091-.088.202-.159.33-.21a1.04 1.04 0 0 1 .396-.079c.294 0 .528.109.7.326.171.217.257.51.257.88 0 .254-.042.475-.127.665-.086.19-.201.335-.347.437a.85.85 0 0 1-.502.154c-.125 0-.243-.02-.355-.06a.857.857 0 0 1-.288-.164v1.003c0 .043-.008.073-.025.09-.017.016-.046.025-.09.025H.115c-.04 0-.068-.009-.086-.025-.019-.017-.028-.047-.028-.09zm1.113.32a.868.868 0 0 0-.447.124v1.206a.834.834 0 0 0 .447.124c.17 0 .296-.058.376-.174.081-.117.121-.3.121-.55 0-.254-.04-.439-.118-.555-.08-.116-.206-.174-.379-.174zm2.248-.087c.121-.134.236-.23.344-.286a.733.733 0 0 1 .345-.085h.063c.043 0 .073.009.092.025.018.017.027.047.027.09v.385c0 .04-.008.068-.025.087-.017.018-.046.027-.089.027a.923.923 0 0 1-.082-.004 1.369 1.369 0 0 0-.383.025c-.1.02-.186.045-.256.076v1.54c0 .04-.008.069-.025.087-.016.018-.046.028-.089.028h-.437c-.04 0-.069-.01-.087-.028-.018-.018-.028-.047-.028-.087V9.508c0-.043.01-.073.028-.09.018-.017.047-.025.087-.025h.328c.07 0 .112.034.128.1zm1.526-.71a.396.396 0 0 1-.278-.096.338.338 0 0 1-.105-.262c0-.11.035-.197.105-.26a.395.395 0 0 1 .278-.097c.116 0 .208.032.278.096.07.064.105.151.105.261a.34.34 0 0 1-.105.262.396.396 0 0 1-.278.096zm-.333.477c0-.043.01-.073.027-.09.019-.017.048-.025.087-.025h.438c.043 0 .072.008.089.025s.025.047.025.09v2.113c0 .04-.008.069-.025.087-.017.018-.046.028-.09.028h-.437c-.04 0-.068-.01-.087-.028-.018-.018-.027-.047-.027-.087zm1.837.11c.161-.107.306-.183.435-.227.13-.045.263-.067.4-.067.273 0 .466.098.579.294.155-.104.3-.18.438-.225.137-.046.278-.069.424-.069.213 0 .377.06.495.179.117.12.175.286.175.5v1.618c0 .04-.008.069-.025.087-.017.019-.046.027-.089.027h-.438c-.04 0-.068-.008-.086-.027-.018-.018-.028-.047-.028-.087V10.15c0-.208-.092-.312-.278-.312-.164 0-.33.04-.497.119v1.664c0 .04-.008.069-.025.087-.017.019-.046.027-.09.027h-.437c-.04 0-.068-.008-.086-.027-.019-.018-.028-.047-.028-.087V10.15c0-.208-.093-.312-.278-.312-.17 0-.337.04-.502.123v1.66c0 .04-.008.069-.025.087-.017.019-.046.027-.089.027h-.438c-.039 0-.068-.008-.086-.027-.018-.018-.027-.047-.027-.087V9.508c0-.043.009-.073.027-.09.018-.017.047-.025.086-.025h.329c.07 0 .112.034.128.101zm4.387 1.16a1.81 1.81 0 0 1-.451-.05c.018.204.08.35.185.44.105.088.263.132.476.132.085 0 .168-.005.249-.016a3.08 3.08 0 0 0 .362-.078.143.143 0 0 1 .023-.002c.052 0 .078.035.078.105v.211c0 .049-.007.083-.02.103a.169.169 0 0 1-.08.053 1.953 1.953 0 0 1-.708.128c-.377 0-.666-.103-.868-.312-.203-.207-.304-.505-.304-.893 0-.398.104-.71.31-.935.207-.227.494-.34.862-.34.283 0 .504.069.664.206a.69.69 0 0 1 .24.55c0 .23-.087.403-.258.52-.172.119-.425.177-.76.177zm.064-.99c-.292 0-.46.18-.506.54.122.025.257.037.406.037.155 0 .267-.024.337-.071.07-.047.105-.12.105-.218 0-.193-.114-.289-.342-.289zm2.948 1.946a.21.21 0 0 1-.075-.011.119.119 0 0 1-.05-.037.274.274 0 0 1-.038-.071l-.777-2.04a1.863 1.863 0 0 1-.023-.063.162.162 0 0 1-.009-.05c0-.047.03-.07.091-.07h.454c.049 0 .084.01.107.028.023.018.04.049.052.092l.468 1.622.477-1.622a.175.175 0 0 1 .052-.092c.023-.018.058-.027.107-.027h.44c.061 0 .091.022.091.068a.16.16 0 0 1-.009.05l-.022.065-.777 2.039a.274.274 0 0 1-.039.07.122.122 0 0 1-.047.038.207.207 0 0 1-.078.01zm2.02-2.703a.393.393 0 0 1-.277-.097.338.338 0 0 1-.105-.26c0-.11.035-.198.105-.262a.393.393 0 0 1 .277-.096c.115 0 .207.032.277.096.07.064.104.151.104.261 0 .11-.034.197-.104.261a.393.393 0 0 1-.277.097zm-.218 2.703c-.04 0-.068-.01-.086-.028-.019-.018-.028-.047-.028-.087V9.507c0-.043.01-.072.028-.09.018-.016.047-.024.086-.024h.436c.042 0 .072.008.089.025.016.017.024.046.024.09v2.111c0 .04-.008.07-.024.087-.017.019-.047.028-.09.028zm1.948.05a.869.869 0 0 1-.513-.153.97.97 0 0 1-.334-.426 1.6 1.6 0 0 1-.116-.63c0-.38.09-.682.268-.91a.856.856 0 0 1 .709-.341.98.98 0 0 1 .622.206V8.458c0-.043.01-.073.027-.09.018-.016.047-.025.087-.025h.436c.042 0 .071.009.088.025.017.017.025.047.025.09v3.161c0 .04-.008.07-.025.087-.017.019-.046.028-.088.028h-.364a.135.135 0 0 1-.084-.023.137.137 0 0 1-.043-.078l-.027-.105a.958.958 0 0 1-.668.256zm.218-.504a.762.762 0 0 0 .418-.128v-1.21a.872.872 0 0 0-.45-.114c-.16 0-.28.06-.358.18-.08.121-.118.304-.118.548 0 .245.041.426.124.546.084.119.212.178.384.178zm2.588-.51c-.169 0-.315-.016-.44-.05.018.201.078.345.18.432.103.087.257.13.465.13.083 0 .164-.005.242-.016a2.997 2.997 0 0 0 .354-.076.135.135 0 0 1 .022-.002c.05 0 .075.035.075.103v.207c0 .048-.007.082-.02.101a.165.165 0 0 1-.077.052 1.895 1.895 0 0 1-.69.126c-.367 0-.65-.102-.846-.306-.197-.204-.296-.496-.296-.876 0-.39.1-.695.302-.917.202-.222.482-.333.84-.333.276 0 .492.068.647.203a.678.678 0 0 1 .234.539c0 .225-.084.395-.251.51-.168.115-.415.173-.74.173zm.063-.97c-.285 0-.45.176-.494.53.119.024.25.036.396.036.15 0 .26-.024.329-.07.068-.046.102-.117.102-.213 0-.19-.111-.284-.333-.284zm2.442 2.003c-.36 0-.642-.11-.845-.328-.203-.218-.304-.523-.304-.914 0-.388.101-.691.304-.91.203-.218.485-.327.845-.327s.642.109.845.327c.203.219.304.522.304.91 0 .39-.101.696-.304.914-.203.218-.485.328-.845.328zm0-.514c.318 0 .477-.242.477-.728 0-.483-.16-.724-.477-.724-.318 0-.477.241-.477.724 0 .486.16.728.477.728zm-6.844 1.886c.405-.306.944-.408 1.39-.408.418 0 .756.09.828.185.15.2-.039 1.584-.775 2.244-.112.102-.22.047-.17-.087.166-.442.536-1.436.36-1.677-.175-.242-1.158-.115-1.6-.058-.068.008-.107-.02-.112-.061v-.023c.004-.036.03-.078.079-.115zm-10.184-.172a.105.105 0 0 1 .106-.091c.027 0 .057.009.089.028a11.778 11.778 0 0 0 6.194 1.772c1.52 0 3.19-.34 4.726-1.043.232-.105.426.164.2.346-1.371 1.09-3.359 1.67-5.07 1.67-2.397 0-4.557-.956-6.191-2.547a.173.173 0 0 1-.054-.097Z",
  apple_tv: "M20.57 17.735h-1.815l-3.34-9.203h1.633l2.02 5.987c.075.231.273.9.586 2.012l.297-.997.33-1.006 2.094-6.004H24zm-5.344-.066a5.76 5.76 0 0 1-1.55.207c-1.23 0-1.84-.693-1.84-2.087V9.646h-1.063V8.532h1.121V7.081l1.476-.602v2.062h1.707v1.113H13.38v5.805c0 .446.074.75.214.932.14.182.396.264.75.264.207 0 .495-.041.883-.115zm-7.29-5.343c.017 1.764 1.55 2.358 1.567 2.366-.017.042-.248.842-.808 1.658-.487.71-.99 1.418-1.79 1.435-.783.016-1.03-.462-1.93-.462-.89 0-1.17.445-1.913.478-.758.025-1.344-.775-1.838-1.484-.998-1.451-1.765-4.098-.734-5.88.51-.89 1.426-1.451 2.416-1.46.75-.016 1.468.512 1.93.512.461 0 1.327-.627 2.234-.536.38.016 1.452.157 2.136 1.154-.058.033-1.278.743-1.27 2.219M6.468 7.988c.404-.495.685-1.18.61-1.864-.585.025-1.294.388-1.723.883-.38.437-.71 1.138-.619 1.806.652.05 1.328-.338 1.732-.825Z",
  plex: "M3.987 8.409c-.96 0-1.587.28-2.12.933v-.72H0v8.88s.038.018.127.037c.138.03.821.187 1.331-.249.441-.377.542-.814.542-1.318v-1.283c.533.573 1.147.813 2 .813 1.84 0 3.253-1.493 3.253-3.48 0-2.12-1.36-3.613-3.266-3.613Zm16.748 5.595.406.591c.391.614.894.906 1.492.908.621-.012 1.064-.562 1.226-.755 0 0-.307-.27-.686-.72-.517-.614-1.214-1.755-1.24-1.803l-1.198 1.779Zm-3.205-1.955c0-2.08-1.52-3.64-3.52-3.64s-3.467 1.587-3.467 3.573a3.48 3.48 0 0 0 3.507 3.52c1.413 0 2.626-.84 3.253-2.293h-2.04l-.093.093c-.427.4-.72.533-1.227.533-.787 0-1.373-.506-1.453-1.266h4.986c.04-.214.054-.307.054-.52Zm-7.671-.219c0 .769.11 1.701.868 2.722l.056.069c-.306.526-.742.88-1.248.88-.399 0-.814-.211-1.138-.579a2.177 2.177 0 0 1-.538-1.441V6.409H9.86l-.001 5.421Zm9.283 3.46h-2.39l2.247-3.332-2.247-3.335h2.39l2.248 3.335-2.248 3.332Zm1.593-1.286Zm-17.162-.342c-.933 0-1.68-.773-1.68-1.72s.76-1.666 1.68-1.666c.92 0 1.68.733 1.68 1.68 0 .946-.733 1.706-1.68 1.706Zm18.361-1.974L24 8.622h-2.391l-.87 1.293 1.195 1.773Zm-9.404-.466c.16-.706.72-1.133 1.493-1.133.773 0 1.373.467 1.507 1.133h-3Z",
  jellyfin: "M12 .002C8.826.002-1.398 18.537.16 21.666c1.56 3.129 22.14 3.094 23.682 0C25.384 18.573 15.177 0 12 0zm7.76 18.949c-1.008 2.028-14.493 2.05-15.514 0C3.224 16.9 9.92 4.755 12.003 4.755c2.081 0 8.77 12.166 7.759 14.196zM12 9.198c-1.054 0-4.446 6.15-3.93 7.189.518 1.04 7.348 1.027 7.86 0 .511-1.027-2.874-7.19-3.93-7.19z",
  spotify: "M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z",
  twitch: "M11.571 4.714h1.715v5.143H11.57zm4.715 0H18v5.143h-1.714zM6 0L1.714 4.286v15.428h5.143V24l4.286-4.286h3.428L22.286 12V0zm14.571 11.143l-3.428 3.428h-3.429l-3 3v-3H6.857V1.714h13.714Z",
  paramount_plus: "M16.347 21.373c.057-.084.151-.314-.025-.74l-.53-1.428c-.073-.182.084-.293.19-.173 0 0 1.004 1.157 1.264 1.64l.495.822c.425.028 1.6.06 2.732.06a3.26 3.26 0 0 1-.316-.364c-1.93-2.392-3.154-3.724-3.166-3.737-.391-.426-.572-.508-.87-.643a4.82 4.82 0 0 1-.138-.065v.364c0 .047-.057.073-.086.022l-2.846-5.001a1.598 1.598 0 0 0-.508-.587l-.277-.194-1.354 3.123c.212 0 .354.216.27.409l-1.25 2.893h1.147c.443 0 .883.087 1.294.255l.302.125s-.913 1.878-.913 2.867c0 .181.028.362.075.534h2.104l-.096-.595s1.266.294 2.502.413M12 2.437c-6.627 0-12 5.373-12 12 0 2.669.873 5.133 2.346 7.126.503-.218.783-.542.983-.791l2.234-2.858a.467.467 0 0 1 .179-.138l.336-.146 3.674-4.659.534-.417 1.094-1.524a.482.482 0 0 1 .101-.102l.478-.347a.34.34 0 0 1 .398-.004l.578.407c.308.216.557.504.726.84l2.322 4.077c.051.09.09.129.182.174.454.227.732.268 1.33.913.277.304 1.495 1.666 3.203 3.784.236.318.538.588.963.783A11.948 11.948 0 0 0 24 14.437c0-6.627-5.373-12-12-12M3.236 15.1l-.778-.253-.48.662v-.818l-.778-.253.778-.253v-.818l.48.662.778-.253-.48.662Zm-.185 2.676-.252.778-.253-.778h-.818l.661-.481-.253-.777.663.48.66-.48-.252.777.662.481Zm.156-6.195.253.778-.661-.48-.663.48.253-.778-.66-.48h.817l.253-.778.252.777h.818Zm1.314-1.76L4.04 9.16l-.778.253.48-.661-.48-.663.778.254.48-.662v.818l.778.253-.777.252Zm2.045-2.862-.253.777-.252-.777h-.818l.662-.48-.253-.778.661.48.661-.48-.252.777.662.48Zm2.577-1.313-.48.661V5.49l-.779-.254.778-.253v-.817l.48.66.78-.253-.481.663.48.66zm3.265-.75.253.778-.661-.48-.662.48.252-.777-.66-.481h.818L12 3.637l.252.778h.818zm2.93.595v.816l-.481-.661-.777.252.48-.662-.48-.662.777.253.48-.66v.817l.779.252zm5.426 8.285.778.253.48-.662v.818l.778.253-.778.253v.818l-.48-.662-.778.253.48-.662zm-3.077-6.04-.253-.777h-.818l.662-.48-.253-.778.662.48.662-.48-.254.778.662.48h-.818zm1.792 2.086v-.818l-.777-.252.777-.253V7.68l.481.662.777-.254-.48.663.48.66-.777-.252zm1.469 1.278.253-.777.254.777h.816l-.66.481.252.778-.662-.48-.661.48.253-.778-.662-.48zm.506 6.676-.253.778-.253-.778h-.817l.662-.481-.253-.777.66.48.663-.48-.253.777.661.481zm-12.08-.615.76-1.588c.024-.048-.032-.108-.067-.067l-.664.668c-.313.329-.847 1.25-.95 1.421l-.808 1.335a.109.109 0 0 1 .1.162l-.739 1.238c-.18.309.145.523.189.452 1.157-1.868 1.832-1.719 1.832-1.719l.387-.897c.022-.047-.001-.1-.05-.12-.12-.05-.316-.27.01-.885z",
};
const WORDMARKS = { disney_plus: "font-family:Georgia,serif;font-style:italic", max: "text-transform:lowercase;letter-spacing:-.04em" };
const COMP_DEFAULTS = {
  visual: null, visual_enabled: false, video_loop: true, video_random_start: true, original_volume: 20,
  soundtrack: null, soundtrack_loop: true, soundtrack_random_start: true, soundtrack_volume: 100, visuals: [],
};
const VISUALS_MAX = 50;  // catalog.VISUALS_MAX
// the mode's random-visual pool: adds or removes one id, keeps the order, never grows past the backend's cap
const toggleIn = (list, id) => {
  const l = Array.isArray(list) ? list : [];
  if (l.includes(id)) return l.filter((x) => x !== id);
  return l.length < VISUALS_MAX ? [...l, id] : l;
};
// a mode's videos in order: its random rotation, else its one visual (the one playing is not always the first)
const modeIds = (comp) => (comp?.visuals?.length ? comp.visuals : comp?.visual ? [comp.visual] : []);
const nextId = (ids, cur) => ids[(ids.indexOf(cur) + 1) % ids.length];
// the mode on the top shelf: the playing one, else the one auto ambience would pick now, else the first
const featuredMode = (a = {}) => {
  const playing = !!a.mode && a.mode !== "off";
  const id = playing ? a.mode : a.daypart_mode || a.modes?.[0]?.id;
  return { m: a.modes?.find((x) => x.id === id) || (id ? { id, name: pretty(id) } : null), playing };
};
// the mode's YouTube videos as editor rows; set_mode_videos takes back the name (a YouTube visual's title) and link
const modeVideos = (comp, visuals) => modeIds(comp)
  .map((id) => visuals?.find((v) => v.id === id && v.source_type === "youtube")).filter(Boolean)
  .map((v) => ({ id: v.id, name: v.title, url: v.link, preview: v.preview }));
const randomLabel = (comp) => (comp?.visuals?.length >= 2 ? `Random from ${comp.visuals.length} videos` : "");
// the TV volume is known only with the TV media player, which set_volume needs: no level, no slider
const volumeSlider = (level) => (level == null ? ""
  : `<input type="range" min="0" max="100" step="1" data-volume aria-label="TV volume"><output data-vol></output>`);
const ARROWS = { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right" };

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const mixToVolumes = (v) => {
  const s = clamp(Math.round(Number(v) || 0), 0, 100);
  return { original_volume: 100 - s, soundtrack_volume: s };
};
const nextTarget = (current, targets, dir) => {
  const list = ["tv", ...["box", "pc"].filter((t) => targets?.[t])];
  const i = list.indexOf(current);
  return i < 0 ? "tv" : list[(i + dir + list.length) % list.length];
};
// where typed text/keys land for the card's target; with a keyboard bridge the card omits the target and this
// mirrors hub._text_target
const textRoute = (snap, target) => {
  if (target === "pc") return "pc";
  if (target === "box") return "keyboard";
  if (!snap?.targets?.keyboard) return "tv";
  if (snap.activity?.current === "pc") return "pc";
  return snap.tv?.keyboard_active ? "tv" : "keyboard";
};
const canSeek = (snap) => snap?.activity?.current !== "pc";  // the PC has no rewind/fast-forward
const safeUrl = (u) => {
  if (!u) return null;
  try {
    const x = new URL(u, location.origin);
    return x.origin === location.origin ? x.href : null;
  } catch {
    return null;
  }
};
const pretty = (s) => String(s ?? "").replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
const icon = (name) => `<ha-icon icon="${esc(name)}"></ha-icon>`;
const img = (u, cls = "") => {
  const src = safeUrl(u);
  return src ? `<img${cls ? ` class="${cls}"` : ""} src="${esc(src)}" alt="" loading="lazy">` : "";
};
const pad2 = (n) => String(n).padStart(2, "0");
const fmtTime = (sec) => {
  const s = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}:${pad2(m)}:${pad2(s % 60)}` : `${m}:${pad2(s % 60)}`;
};
const fmtMins = (m) => (m >= 60 ? `${Math.floor(m / 60)} h ${pad2(m % 60)} min` : `${m} min`);
const hint = (text, ic = "mdi:information-outline") => `<p class="hint">${icon(ic)}<span>${esc(text)}</span></p>`;
const banner = (text, ic = "mdi:alert-circle-outline", cls = "banner err") => `<div class="${cls}">${icon(ic)}<span>${esc(text)}</span></div>`;

class HomeHubMediaCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._slices = {};
    this._tab = "remote";
    this._browse = {};
    this._pointers = new Map();
    this._mv = { dx: 0, dy: 0 };
    this._sc = 0;
    this._channel = "";
    this._noClickUntil = 0;
    this._arrows = [];  // swipe-mode arrow keys waiting for the one request in flight
    this._touchpad = false;
    try { this._touchpad = localStorage.getItem(PAD_KEY) === "touchpad"; } catch { /* storage blocked: D-pad */ }
    this._stopHoldB = () => this._stopHold();
    const r = this.shadowRoot;
    r.addEventListener("click", (e) => this._onClick(e));
    r.addEventListener("pointerdown", (e) => this._onDown(e));
    r.addEventListener("pointermove", (e) => this._onMove(e));
    r.addEventListener("pointerup", (e) => this._onUp(e));
    r.addEventListener("pointercancel", (e) => this._onUp(e));
    r.addEventListener("keydown", (e) => this._onKey(e));
    r.addEventListener("input", (e) => this._onInput(e));
    r.addEventListener("contextmenu", (e) => {
      if (e.target.closest?.("[data-hold],[data-touch]")) e.preventDefault();
    });
    r.addEventListener("wheel", (e) => {
      if (!e.target.closest?.("[data-touch]") || this._eff() !== "pc") return;
      e.preventDefault();
      this._queueScroll(e.deltaY * 0.72);
    }, { passive: false });
    // broken artwork falls back to the icon underneath
    r.addEventListener("error", (e) => { if (e.target.tagName === "IMG") e.target.remove(); }, true);
  }

  static getStubConfig() { return {}; }
  // drawer: the host is position:fixed, so an "auto" row collapses to nothing ("rows: 0" would emit invalid `span 0`)
  getGridOptions() { return this._config?.drawer ? { columns: 12, rows: "auto" } : { columns: 12, min_columns: 6, rows: "auto" }; }
  getCardSize() { return this._config?.drawer ? 0 : 8; }

  setConfig(config) {
    const tabs = config?.tabs ?? Object.keys(TABS);
    if (!Array.isArray(tabs) || !tabs.length || tabs.some((t) => !TABS[t])) {
      throw new Error("tabs: use any of remote, ambience, library");
    }
    if (config.target != null && !TARGETS[config.target]) throw new Error("target: use tv, box or pc");
    const def = config.default_tab ?? "remote";
    if (!TABS[def]) throw new Error("default_tab: use remote, ambience or library");
    this._config = { ...config, drawer: !!config.drawer, tabs };
    this._tab = tabs.includes(def) ? def : tabs[0];
    this._open = false;
    try { this._open = localStorage.getItem(DRAWER_KEY) === "open"; } catch { /* storage blocked */ }
    this.toggleAttribute("drawer", this._config.drawer);
    this._built = false;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._subscribe();
  }

  get hass() { return this._hass; }

  connectedCallback() {
    this._retryAt = 0;  // re-adding the card retries a failed subscribe right away
    this._subscribe();
    this._render();
    clearInterval(this._ticker);
    this._ticker = setInterval(() => this._tick(), 1000);
  }

  disconnectedCallback() {
    clearInterval(this._ticker);
    clearTimeout(this._chTimer);
    clearTimeout(this._mvTimer);
    clearTimeout(this._scTimer);
    this._mvTimer = this._scTimer = this._swipe = null;
    this._mv = { dx: 0, dy: 0 };
    this._sc = 0;
    this._pointers.clear();
    this._stopHold();
    if (this._edit) this._flushLive(this._edit);
    this._unsubscribe();
  }

  // ---------- data ----------

  async _subscribe() {
    // hass is set on every HA state change: after a failure wait 30 s instead of retrying each time
    if (this._sub || !this._hass?.connection || !this.isConnected || Date.now() < (this._retryAt || 0)) return;
    const sub = this._hass.connection.subscribeMessage((e) => this._onSnapshot(e), { type: `${DOMAIN}/subscribe` });
    this._sub = sub;  // the pending promise; _unsubscribe() resolves it, even mid-subscribe
    try {
      await sub;
    } catch (err) {
      if (this._sub !== sub) return;  // a stale attempt from before a disconnect/reconnect
      this._sub = null;
      this._retryAt = Date.now() + 30000;
      this._fatal = err?.code === "unknown_command"
        ? "HomeHub Media is not set up. Add the integration first."
        : `HomeHub Media is unavailable (${err?.message || "connection error"}).`;
      this._render();
    }
  }

  _unsubscribe() {
    const sub = this._sub;
    this._sub = null;
    sub?.then((unsub) => unsub()).catch(() => {});
  }

  _onSnapshot(snap) {
    const first = !this._snap;
    this._snap = snap;
    this._snapAt = Date.now();
    this._fatal = null;
    this._render();
    if (first) this._loadFor(this._tab);
  }

  _loadFor(tab) {
    if (tab === "ambience") this._load("catalog");
    if (tab === "library") this._load("library");
  }

  // kind "catalog" -> this._catalog / this._catalogErr, "library" -> this._library / this._libraryErr
  async _load(kind) {
    if (!this._hass) return;
    try {
      this[`_${kind}`] = await this._hass.callWS({ type: `${DOMAIN}/${kind}` });
      this[`_${kind}Err`] = null;
      this._catVer = (this._catVer || 0) + 1;  // cheap identity for the editor slice
    } catch (err) {
      this[`_${kind}Err`] = err?.message || `Could not load the ${kind}`;
    }
    this._render();
  }

  async _call(service, data = {}, domain = DOMAIN) {
    if (!this._hass) return false;
    try {
      await this._hass.callService(domain, service, data);
      return true;
    } catch (err) {
      this._toast(err?.message || `${pretty(service)} failed`);
      return false;
    }
  }

  _toast(message, action) {
    const now = Date.now();
    if (message === this._lastToast && now - this._lastToastAt < 3000) return;  // no spam from pointer batches
    this._lastToast = message;
    this._lastToastAt = now;
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: action ? { message, action } : { message }, bubbles: true, composed: true }));
  }

  // ---------- state helpers ----------

  _tabs() {
    // before the first snapshot nothing is known yet, so keep default_tab
    return this._config.tabs.filter((t) => t !== "library" || !this._snap || this._snap.servers?.length);
  }

  _eff() {
    const s = this._snap;
    const act = s?.activity?.current;
    const t = this._target || this._config?.target || (TARGETS[act] ? act : "tv");
    return t === "tv" || s?.targets?.[t] ? t : "tv";
  }

  _setTab(tab) {
    if (!TABS[tab]) return;
    this._tab = tab;
    this._render();
    this._loadFor(tab);
  }

  _setTarget(t) {
    this._target = t;
    this._channel = "";
    this._render();
  }

  _remote(command, target = this._eff()) {
    return this._call("remote", { command, target });
  }

  _textTarget() {
    const t = this._eff();
    // undefined: the hub picks TV IME or keyboard bridge from its live state
    return t === "tv" && this._snap?.targets?.keyboard ? undefined : textRoute(this._snap, t);
  }

  _kbd(service, data) {
    // the TV's on-screen keyboard only has Enter, so clear/backspace there would just error
    if (service === "keyboard_key" && data.key !== "enter" && textRoute(this._snap, this._eff()) === "tv") return undefined;
    const target = this._textTarget();
    return this._call(service, target ? { ...data, target } : data);
  }

  _toggleDrawer(open = !this._open) {
    this._open = open;
    try { localStorage.setItem(DRAWER_KEY, open ? "open" : "closed"); } catch { /* storage blocked */ }
    const d = this.shadowRoot.querySelector(".drawer");
    if (!d) return;
    d.dataset.open = open;
    const h = d.querySelector(".handle");
    h.setAttribute("aria-expanded", open);
    h.setAttribute("aria-label", `${open ? "Close" : "Open"} media controls`);
    d.querySelector(".panel").inert = !open;
  }

  // ---------- rendering ----------

  _sec(key, slice, html) {
    const el = this.shadowRoot.querySelector(`[data-s="${key}"]`);
    if (!el) return false;
    const s = JSON.stringify(slice);
    if (this._slices[key] === s) return false;
    this._slices[key] = s;
    // typed text survives a re-render: [data-keep] fields get their value, focus and caret back
    const f = this.shadowRoot.activeElement;
    const kept = [...el.querySelectorAll("[data-keep]")].map((n) => [n.dataset.keep, n.value, n === f && [n.selectionStart, n.selectionEnd]]);
    el.innerHTML = html();
    for (const [k, value, sel] of kept) {
      const n = el.querySelector(`[data-keep="${k}"]`);
      if (!n) continue;
      n.value = value;
      if (sel) { n.focus(); n.setSelectionRange(...sel); }
    }
    return true;
  }

  _build() {
    const inner = `<div class="wrap">
        <div data-s="banner"></div>
        <div data-s="np"></div>
        <div class="tabs" role="tablist" aria-label="HomeHub Media" data-s="tabs"></div>
        <div class="body" id="panel" data-s="body"></div>
      </div>`;
    const open = !!this._open;
    this.shadowRoot.innerHTML = `<style>${CSS}</style>${this._config.drawer
      ? `<div class="drawer" data-open="${open}">
          <button class="handle" data-a="drawer" aria-expanded="${open}" aria-label="${open ? "Close" : "Open"} media controls">${icon("mdi:chevron-left")}</button>
          <ha-card class="panel"${open ? "" : " inert"}>${inner}</ha-card>
        </div>`
      : `<ha-card>${inner}</ha-card>`}`;
    this._slices = {};
    this._built = true;
  }

  _render() {
    if (!this._config || !this.isConnected) return;
    if (!this._built) this._build();
    const s = this._snap;
    const tabs = this._tabs();
    if (!tabs.includes(this._tab)) this._tab = tabs[0];
    if (this._edit && !s?.ambience?.modes?.some((m) => m.id === this._edit.id)) this._closeEditor(false);

    this._sec("banner", [this._fatal, s?.tv?.available], () => {
      if (this._fatal) return banner(this._fatal);
      if (s && s.tv?.available === false) return banner("TV is off or unreachable", "mdi:television-off", "banner");
      return "";
    });
    this._sec("np", [canSeek(s), (s?.now_playing ?? []).slice(0, 1).map(({ position, ...rest }) => rest)], () => this._npHtml());
    this._tickNp();
    this._sec("tabs", [tabs.length > 1 && tabs, this._tab], () => (tabs.length < 2 ? "" : tabs.map((t) => {
      const on = t === this._tab;
      return `<button class="tab" role="tab" id="tab-${t}" aria-controls="panel" data-a="tab" data-v="${t}" aria-selected="${on}" tabindex="${on ? 0 : -1}">${icon(TABS[t][1])}<span>${TABS[t][0]}</span></button>`;
    }).join("")));
    const body = this.shadowRoot.querySelector(".body");
    if (tabs.length > 1) {
      body.setAttribute("role", "tabpanel");
      body.setAttribute("aria-labelledby", `tab-${this._tab}`);
    } else {
      body.removeAttribute("role");
      body.removeAttribute("aria-labelledby");
    }
    if (this._sec("body", [this._tab, !!s, !!this._fatal], () => this._bodyHtml())) {
      for (const k of Object.keys(this._slices)) if (!["banner", "np", "tabs", "body"].includes(k)) delete this._slices[k];
    }
    if (!s) return;
    if (this._tab === "remote") this._renderRemote();
    else if (this._tab === "ambience") this._renderAmbience();
    else this._renderLibrary();
  }

  _bodyHtml() {
    if (!this._snap) return this._fatal ? "" : `<div class="skel"></div><div class="skel short"></div>`;
    if (this._tab === "ambience") return `<div data-s="amberr"></div><div data-s="hero"></div><div data-s="sleep"></div><div class="ambmain" data-s="ambmain"></div>`;
    if (this._tab === "library") return `<div class="lib" data-s="lib"></div>`;
    return `<div class="remote">
      <div class="col">
        <div class="pills" role="radiogroup" aria-label="Control" data-s="targets"></div>
        <div class="row">
          <button class="ib power" data-a="remote" data-v="power" aria-label="Power">${icon("mdi:power")}</button>
          <span class="grow"></span>
          <button class="ib" data-a="remote" data-v="back" aria-label="Back">${icon("mdi:arrow-u-left-top")}</button>
          <button class="ib" data-a="remote" data-v="home" aria-label="Home">${icon("mdi:home-outline")}</button>
        </div>
        <div class="pad" data-s="pad"></div>
        <div data-s="padhint"></div>
        <div class="ctl" data-s="ctl"></div>
      </div>
      <div class="col">
        <div data-s="apps"></div>
        <div class="kbdwrap" data-kbd>
          <div class="kbd">${icon("mdi:keyboard-outline")}
            <input data-kbdin type="text" maxlength="200" enterkeyhint="send" autocomplete="off" autocorrect="off"
              autocapitalize="off" spellcheck="false" aria-label="Type text">
            <button class="ib flat" data-a="kclear" aria-label="Clear text">${icon("mdi:close")}</button>
            <button class="btn sm primary" data-a="ksend">Send</button>
          </div>
          <div data-s="kbdhint"></div>
        </div>
        <div data-s="extra"></div>
      </div>
    </div>`;
  }

  _npHtml() {
    const n = this._snap?.now_playing?.[0];
    if (!n) return "";
    const playing = n.state === "playing";
    const sub = [n.subtitle, n.player].filter(Boolean).join(" · ");
    const seek = canSeek(this._snap);
    // no target: the hub routes these by the current activity
    return `<div class="np">
      ${img(n.image, "npbg")}
      <div class="npart">${icon("mdi:movie-open-outline")}${img(n.image)}</div>
      <div class="npmeta">
        <b>${esc(n.title)}</b><small>${esc(sub || pretty(n.server))}</small>
        <div class="npbar"${n.duration ? "" : " hidden"}><span class="npfill"></span></div>
        <div class="nptime"><span data-npt></span><span>${n.duration ? fmtTime(n.duration) : ""}</span></div>
      </div>
      <div class="npbtns">
        ${seek ? `<button class="ib flat" data-a="np" data-v="rewind" aria-label="Back 30 seconds">${icon("mdi:rewind-30")}</button>` : ""}
        <button class="ib accent" data-a="np" data-v="${playing ? "pause" : "play"}" aria-label="${playing ? "Pause" : "Play"}">${icon(playing ? "mdi:pause" : "mdi:play")}</button>
        ${seek ? `<button class="ib flat" data-a="np" data-v="fast_forward" aria-label="Forward 30 seconds">${icon("mdi:fast-forward-30")}</button>` : ""}
      </div>
    </div>`;
  }

  _tickNp() {
    const n = this._snap?.now_playing?.[0];
    if (!n) return;
    const live = n.state === "playing" ? (Date.now() - this._snapAt) / 1000 : 0;
    const pos = Math.min(n.duration || Infinity, (n.position || 0) + live);
    const fill = this.shadowRoot.querySelector(".npfill");
    if (fill && n.duration) fill.style.width = `${clamp((pos / n.duration) * 100, 0, 100)}%`;
    const t = this.shadowRoot.querySelector("[data-npt]");
    if (t) t.textContent = fmtTime(pos);
  }

  _tick() {
    this._tickNp();
    if (this._snap?.ambience?.sleep_until && this._tab === "ambience") this._render();
  }

  // ---------- remote tab ----------

  _renderRemote() {
    const s = this._snap;
    const tg = s.targets || {};
    const t = this._eff();
    const avail = ["tv", ...["box", "pc"].filter((k) => tg[k])];
    this._sec("targets", [avail, t], () => (avail.length < 2 ? "" : avail.map((k) => {
      const [label, aria, ic] = TARGETS[k];
      return `<button class="pill" role="radio" aria-checked="${k === t}" tabindex="${k === t ? 0 : -1}" aria-label="${aria}" data-a="target" data-v="${k}">${icon(ic)}<span>${label}</span></button>`;
    }).join("")));

    const canTouch = t !== "box";
    const tp = this._touchpad && canTouch;
    this._sec("pad", [tp, t === "pc", avail.length > 1], () => (tp ? this._touchHtml(t === "pc") : this._dpadHtml(avail.length > 1)));
    const padHint = tp && t === "tv"
      ? (s.tv?.pointer_active ? "Pointer mode: drag to move · tap to click" : "Swipe to move · tap for OK") : "";
    this._sec("padhint", [padHint], () => (padHint ? hint(padHint, "mdi:gesture-swipe") : ""));
    const level = s.tv?.volume;
    this._sec("ctl", [!!s.tv?.muted, tp, canTouch, level != null], () => this._ctlHtml(!!s.tv?.muted, tp, canTouch, level));
    const vol = this.shadowRoot.querySelector("[data-vol]");
    if (vol) vol.textContent = level ?? "–";
    const slider = this.shadowRoot.querySelector("[data-volume]");
    if (slider && level != null && !this._volTimer && Date.now() - (this._volAt || 0) > 1500) {  // never fight a drag
      slider.value = level;
      slider.style.setProperty("--p", `${level}%`);
    }

    const apps = (s.apps || []).filter((a) => a.installed);
    this._sec("apps", [apps, s.tv?.app], () => (apps.length ? `<h3 class="sh">Apps</h3><div class="apps">${apps.map((a) => {
      const [bg, fg] = BRANDS[a.key] || [];
      const on = a.key === s.tv?.app;
      const cap = `<span class="cap">${esc(a.name)}</span>`;
      const art = LOGOS[a.key] ? `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${LOGOS[a.key]}"/></svg>${cap}`
        : a.key === "browser" ? `${icon("mdi:web")}${cap}` : `<span class="wm" style="${WORDMARKS[a.key] || ""}">${esc(a.name)}</span>`;
      return `<button class="app${on ? " on" : ""}"${bg ? ` style="--b:${bg};--f:${fg}"` : ""} data-a="app" data-v="${esc(a.key)}" data-hold="restart"
        aria-label="${esc(a.name)}${on ? ", open now" : ""}. Long-press to restart">${art}</button>`;
    }).join("")}</div>` : ""));

    const kbd = this.shadowRoot.querySelector("[data-kbd]");
    if (kbd) {
      kbd.hidden = t === "box" && !tg.keyboard;
      kbd.querySelector("input").placeholder = `Type on the ${TARGETS[t][1]}`;
    }
    const imeHint = t === "tv" && !tg.keyboard;
    this._sec("kbdhint", [imeHint], () => (imeHint
      ? hint("Works in the TV's own search and browser. YouTube and Netflix need the Bluetooth keyboard bridge.") : ""));
    this._sec("extra", [t, t === "pc" ? s.pc : null], () => (t === "box" ? this._boxHtml() : t === "pc" ? this._pcHtml() : ""));
  }

  _dpadHtml(swipe) {
    const k = (dir, ic, hold) => `<button class="dk ${dir}" tabindex="-1" data-a="remote" data-v="${dir}"${hold ? ` data-hold="repeat"` : ""} aria-label="${pretty(dir)}">${icon(ic)}</button>`;
    return `<div class="dpad" role="group" tabindex="0" ${swipe ? `data-swipe="target"` : ""}
        aria-label="Directional pad. Arrow keys move, Enter selects${swipe ? ". Swipe sideways to switch device" : ""}">
      ${k("up", "mdi:chevron-up", true)}${k("left", "mdi:chevron-left")}
      <button class="ok" tabindex="-1" data-a="remote" data-v="select" aria-label="OK">OK</button>
      ${k("right", "mdi:chevron-right")}${k("down", "mdi:chevron-down", true)}
    </div>`;
  }

  _touchHtml(pc) {
    return `<div class="touch" data-touch role="application" aria-label="${pc ? "Touchpad: drag to move the pointer, tap to click"
      : "Touchpad: swipe for the arrow keys and tap for OK, or move the pointer where the TV allows it"}">
        ${icon("mdi:gesture-tap")}<span>${pc ? "Drag to move · tap to click · two fingers to scroll" : "Swipe or tap here"}</span>
      </div>
      <div class="clicks">
        <button class="btn" data-a="click" data-v="click">Click</button>
        ${pc ? `<button class="btn" data-a="click" data-v="right_click">Right click</button>` : ""}
      </div>`;
  }

  // with the TV media player: mute, slider and level; without it (no level known): the volume rocker
  _ctlHtml(muted, tp, canTouch, level) {
    const muteIc = icon(muted ? "mdi:volume-off" : "mdi:volume-high");
    const more = `<button class="ib" data-a="remote" data-v="play_pause" aria-label="Play or pause">${icon("mdi:play-pause")}</button>
      ${canTouch ? `<button class="ib${tp ? " on" : ""}" data-a="touchpad" aria-pressed="${tp}" aria-label="Touchpad">${icon("mdi:gesture-tap-hold")}</button>` : ""}`;
    if (level != null) {
      return `<div class="volrow"><button class="ib mute${muted ? " on" : ""}" data-a="remote" data-v="mute" aria-pressed="${muted}" aria-label="Mute">${muteIc}</button>${volumeSlider(level)}</div>
        <div class="row center">${more}</div>`;
    }
    return `<div class="row center"><div class="rocker" role="group" aria-label="Volume">
        <button data-a="remote" data-v="volume_down" data-hold="repeat" aria-label="Volume down">${icon("mdi:minus")}</button>
        <button class="mute${muted ? " on" : ""}" data-a="remote" data-v="mute" aria-pressed="${muted}" aria-label="Mute">${muteIc}<span data-vol></span></button>
        <button data-a="remote" data-v="volume_up" data-hold="repeat" aria-label="Volume up">${icon("mdi:plus")}</button>
      </div>${more}</div>`;
  }

  _boxHtml() {
    const key = (v, label, aria) => `<button class="key" data-a="digit" data-v="${v}"${aria ? ` aria-label="${aria}"` : ""}>${label}</button>`;
    return `<h3 class="sh">Channel <output class="chd" data-chd aria-live="polite">${esc(this._channel)}</output></h3>
      <div class="keypad">
        ${[1, 2, 3, 4, 5, 6, 7, 8, 9].map((d) => key(d, d)).join("")}
        ${key("del", icon("mdi:backspace-outline"), "Delete digit")}${key(0, 0)}${key("go", icon("mdi:check"), "Go to channel")}
      </div>
      <div class="chips center">
        <button class="chip" data-a="remote" data-v="channel_down" aria-label="Channel down">${icon("mdi:chevron-down")}CH</button>
        <button class="chip" data-a="remote" data-v="guide">${icon("mdi:television-guide")}Guide</button>
        <button class="chip" data-a="remote" data-v="channel_up" aria-label="Channel up">${icon("mdi:chevron-up")}CH</button>
      </div>`;
  }

  _pcHtml() {
    const pc = this._snap.pc || {};
    const clients = pc.clients || [];
    return `<h3 class="sh">On the media PC</h3>
      <div class="chips">
        ${clients.map((c) => `<button class="chip" data-a="pc_open" data-v="${esc(c)}" aria-pressed="${c === pc.active}">${esc(pretty(c))}</button>`).join("")}
        <button class="chip" data-a="pc_close"${pc.active ? "" : " disabled"}>${icon("mdi:exit-to-app")}Exit</button>
      </div>`;
  }

  _digit(v) {
    if (v === "go") return this._sendChannel();
    if (v === "del") this._channel = this._channel.slice(0, -1);
    else if (this._channel.length < 4) this._channel += v;
    clearTimeout(this._chTimer);
    if (this._channel) this._chTimer = setTimeout(() => this._sendChannel(), 1500);
    this._showChannel();
  }

  _sendChannel() {
    clearTimeout(this._chTimer);
    const number = this._channel;
    this._channel = "";
    this._showChannel();
    if (number) this._call("channel", { number });
  }

  _showChannel() {
    const d = this.shadowRoot.querySelector("[data-chd]");
    if (d) d.textContent = this._channel;
  }

  // ---------- touchpad (batching ported from homehub-media-hub-control-pad.js) ----------

  _pointer(action, extra = {}) {
    return this._call("pointer", { action, ...extra, target: this._eff() });
  }

  _tpDown(e, el) {
    e.preventDefault();
    if (e.isPrimary) this._pointers.clear();  // drop fingers whose pointerup we never saw
    el.setPointerCapture?.(e.pointerId);
    this._pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (this._pointers.size === 1) this._tap = { t: performance.now(), dist: 0 };
    else { this._tap = null; this._lastY = null; }
    this._sw = { x: 0, y: 0 };
  }

  // a TV without pointer input (outside its browser): the touchpad swipes the arrow keys and taps OK
  _swipeMode() {
    return this._eff() === "tv" && !this._snap?.tv?.pointer_active;
  }

  _swipeArrows(dx, dy) {
    const a = this._sw;
    a.x += dx;
    a.y += dy;
    const horiz = Math.abs(a.x) >= Math.abs(a.y);
    const d = horiz ? a.x : a.y;
    const steps = Math.trunc(Math.abs(d) / SWIPE_STEP);
    if (!steps) return;
    this._sw = { x: 0, y: 0 };
    const dir = horiz ? (d > 0 ? "right" : "left") : (d > 0 ? "down" : "up");
    for (let i = 0; i < steps; i++) this._arrows.push(dir);
    this._pumpArrows();
  }

  _pumpArrows() {
    if (this._arrowBusy || !this._arrows.length) return;  // one request in flight; the rest wait their turn
    this._arrowBusy = this._remote(this._arrows.shift(), "tv").finally(() => {
      this._arrowBusy = null;
      this._pumpArrows();
    });
  }

  _tpMove(e) {
    const p = this._pointers.get(e.pointerId);
    e.preventDefault();
    const dx = e.clientX - p.x;
    const dy = e.clientY - p.y;
    p.x = e.clientX;
    p.y = e.clientY;
    if (this._tap) this._tap.dist += Math.abs(dx) + Math.abs(dy);
    if (this._pointers.size >= 2) {
      if (this._eff() !== "pc") return;
      let y = 0;
      this._pointers.forEach((q) => { y += q.y; });
      y /= this._pointers.size;
      if (this._lastY != null) this._queueScroll(-(y - this._lastY) * 1.2);
      this._lastY = y;
      return;
    }
    if (this._swipeMode()) this._swipeArrows(dx, dy);
    else this._queueMove(dx * 1.15, dy * 1.15);
  }

  _tpUp(e) {
    this._pointers.delete(e.pointerId);
    if (this._pointers.size < 2) this._lastY = null;
    const tap = this._tap;
    if (!this._pointers.size && e.type === "pointerup" && tap && tap.dist < 8 && performance.now() - tap.t < 300) {
      this._tap = null;
      this._click("click");
    }
  }

  _queueMove(dx, dy) {
    this._mv.dx += clamp(dx, -120, 120);
    this._mv.dy += clamp(dy, -120, 120);
    if (!this._mvTimer) this._mvTimer = setTimeout(() => this._flushMove(), 36);
  }

  _flushMove() {
    this._mvTimer = null;
    if (this._mvBusy) return;  // one request in flight; the rest accumulates
    const { dx, dy } = this._mv;
    this._mv = { dx: 0, dy: 0 };
    if (Math.abs(dx) < 0.2 && Math.abs(dy) < 0.2) return;
    const r1 = (v) => Math.round(clamp(v, -160, 160) * 10) / 10;
    this._mvBusy = this._pointer("move", { dx: r1(dx), dy: r1(dy) }).finally(() => {
      this._mvBusy = null;
      if (Math.abs(this._mv.dx) >= 0.2 || Math.abs(this._mv.dy) >= 0.2) this._queueMove(0, 0);
    });
  }

  async _flushPendingMove() {
    if (this._mvTimer) {
      clearTimeout(this._mvTimer);
      this._flushMove();
    }
    await this._mvBusy;
    if (Math.abs(this._mv.dx) >= 0.2 || Math.abs(this._mv.dy) >= 0.2) {
      this._flushMove();
      await this._mvBusy;
    }
  }

  _queueScroll(dy) {
    this._sc += clamp(dy, -180, 180);
    if (!this._scTimer) this._scTimer = setTimeout(() => this._flushScroll(), 36);
  }

  _flushScroll() {
    this._scTimer = null;
    if (this._scBusy) return;
    const dy = this._sc;
    this._sc = 0;
    if (Math.abs(dy) < 0.2) return;
    this._scBusy = this._pointer("scroll", { dy: Math.round(clamp(dy, -240, 240) * 10) / 10 }).finally(() => {
      this._scBusy = null;
      if (Math.abs(this._sc) >= 0.2) this._queueScroll(0);
    });
  }

  async _click(action) {
    if (this._swipeMode()) return this._remote("select", "tv");  // no pointer: a tap is OK
    // the pointer must arrive before the click does
    await this._flushPendingMove();
    return this._pointer(action);
  }

  // ---------- ambience tab ----------

  _visual(id) {
    return id ? this._catalog?.visuals?.find((v) => v.id === id) : undefined;
  }

  // the mode's YouTube videos: the optimistic list while a change saves, else the saved one
  _rows(id) {
    return this._vsave?.id === id ? this._vsave.rows : modeVideos(this._snap.ambience?.compositions?.[id], this._catalog?.visuals);
  }

  _ambSlice() {
    const a = this._snap.ambience || {};
    const common = [a.mode, a.visual, a.modes, a.compositions, this._catVer, this._vsave];
    if (!this._edit) return ["grid", ...common, a.auto, a.daypart_mode];
    const ed = this._edit;
    return ["edit", ...common, ed.id, ed.draft, ed.sheet, this._browse, this._catalogErr, a.backend, this._refreshing];
  }

  _renderAmbience() {
    const a = this._snap.ambience || {};
    const on = !this._edit && !!a.mode && a.mode !== "off";
    this._sec("amberr", [a.error], () => (a.error ? banner(a.error) : ""));
    this._sec("hero", [!this._edit, a.mode, a.visual, a.modes, a.daypart_mode, a.compositions, this._catVer],
      () => (this._edit ? "" : this._heroHtml()));
    const until = on && a.sleep_until ? new Date(a.sleep_until).getTime() : null;
    const mins = until ? Math.max(0, Math.ceil((until - Date.now()) / 60000)) : null;
    this._sec("sleep", [on, mins], () => (!on ? "" : `<div class="sleep">
        <h3 class="sh">${icon("mdi:power-sleep")}Sleep timer${mins != null
          ? `<span class="note">Off in ${fmtMins(mins)}</span><button class="ib flat sm" data-a="sleep" data-v="0" aria-label="Cancel the sleep timer">${icon("mdi:close")}</button>` : ""}</h3>
        <div class="slchips">${[15, 30, 60, 90].map((m) => `<button class="chip sm" data-a="sleep" data-v="${m}" aria-label="Sleep in ${m} minutes">${m} min</button>`).join("")}</div>
      </div>`));
    this._sec("ambmain", this._ambSlice(), () => (this._edit ? this._editorHtml() : this._modesHtml()));
  }

  _heroHtml() {
    const a = this._snap.ambience || {};
    const { m, playing } = featuredMode(this._snap.ambience);
    if (!m) return "";
    const ids = m.lights_only ? [] : modeIds(a.compositions?.[m.id]);
    const i = playing ? ids.indexOf(a.visual) : -1;
    const v = this._visual(playing ? a.visual : ids[0]);
    const meta = [m.lights_only ? "Lights only" : ids.length ? `${ids.length} video${ids.length === 1 ? "" : "s"}` : "No videos",
      i >= 0 ? `${i + 1} of ${ids.length}` : ""].filter(Boolean).join(" · ");
    return `<div class="hero">
      <span class="ph">${icon(m.icon || "mdi:lightbulb-group")}</span>${img(v?.preview)}
      <div class="over">
        <span class="eyebrow">${playing ? `<span class="live">${m.lights_only ? "Lights on" : "Now playing"}</span>` : a.daypart_mode === m.id ? "Suggested now" : "Ambience"}</span>
        <b class="hn">${esc(m.name)}</b>
        <small>${esc(meta)}${v?.title && !m.lights_only ? ` · ${esc(v.title)}` : ""}</small>
        <div class="acts">
          ${playing ? `<button class="hb primary" data-a="amb_stop">${icon("mdi:stop")}Stop</button>`
            : `<button class="hb primary" data-a="mode" data-v="${esc(m.id)}" aria-label="Play ${esc(m.name)}">${icon("mdi:play")}Play</button>`}
          ${playing && ids.length >= 2 ? `<button class="hb round" data-a="next" data-v="${esc(nextId(ids, a.visual))}" aria-label="Next video">${icon("mdi:skip-next")}</button>` : ""}
          ${m.lights_only ? "" : `<button class="hb round" data-a="edit" data-v="${esc(m.id)}" aria-label="Videos of ${esc(m.name)}">${icon("mdi:playlist-edit")}</button>`}
        </div>
      </div>
    </div>`;
  }

  _modesHtml() {
    const a = this._snap.ambience || {};
    const dp = a.modes?.find((m) => m.id === a.daypart_mode);
    const { m: fm, playing } = featuredMode(this._snap.ambience);
    const vids = fm && !fm.lights_only ? modeIds(a.compositions?.[fm.id]).map((id) => this._visual(id) || { id }) : [];
    return `<h3 class="sh">Modes</h3>
      <div class="tvrow">${(a.modes || []).map((m) => {
        const on = a.mode === m.id;
        const n = m.lights_only ? 0 : modeIds(a.compositions?.[m.id]).length;
        const sub = on ? "Now playing" : m.lights_only ? "Lights only" : n ? `${n} video${n === 1 ? "" : "s"}` : "No videos";
        return `<div class="tvw"><button class="tvp${on ? " on" : ""}" data-a="mode" data-v="${esc(m.id)}"${m.lights_only ? "" : ` data-hold="edit"`} aria-pressed="${on}"
            aria-label="Start ${esc(m.name)}${on ? ", playing" : ""}${m.lights_only ? "" : ". Hold to edit its videos"}">
            <span class="tva"><span class="ph">${icon(m.icon || "mdi:lightbulb-group")}</span>${n ? img(this._visual(modeIds(a.compositions[m.id])[0])?.preview) : ""}${on ? `<span class="eq" aria-hidden="true"><i></i><i></i><i></i></span>` : ""}</span>
            <b>${esc(m.name)}</b><small>${esc(sub)}</small>
          </button>${m.lights_only ? "" : `<button class="tvedit" data-a="edit" data-v="${esc(m.id)}" aria-label="Edit ${esc(m.name)}">${icon("mdi:pencil")}</button>`}</div>`;
      }).join("")}</div>
      ${vids.length ? `<h3 class="sh">${playing ? "Up next" : `In ${esc(fm.name)}`}</h3>
      <div class="tvrow sm">${vids.map((v) => {
        const on = playing && a.visual === v.id;
        return `<button class="tvp${on ? " on" : ""}" data-a="vid" data-m="${esc(fm.id)}" data-v="${esc(v.id)}" aria-label="Play ${esc(v.title || "video")}">
            <span class="tva"><span class="ph">${icon("mdi:youtube")}</span>${img(v.preview)}${on ? `<span class="eq" aria-hidden="true"><i></i><i></i><i></i></span>` : ""}</span>
            <b>${esc(v.title || "Video")}</b>
          </button>`;
      }).join("")}</div>` : ""}
      <div class="auto">
        <span class="ax"><b>Auto ambience${dp ? `<span> · now ${esc(dp.name)}</span>` : ""}</b><small>Starts a mode by time of day when the TV is idle</small></span>
        <button class="sw" role="switch" aria-checked="${!!a.auto}" data-a="auto" aria-label="Auto ambience"></button>
      </div>`;
  }

  // the switch entity, found by its translation key in case the user renamed it
  _autoAmbience() {
    const ent = Object.values(this._hass?.entities || {}).find((e) => e.platform === DOMAIN && e.translation_key === "auto_ambience");
    return this._call(this._snap.ambience?.auto ? "turn_off" : "turn_on", { entity_id: ent?.entity_id || "switch.homehub_media_auto_ambience" }, "switch");
  }

  _openEditor(id) {
    const saved = this._snap.ambience?.compositions?.[id] || {};
    const draft = { ...COMP_DEFAULTS, ...saved };
    if (!("visual_enabled" in saved)) draft.visual_enabled = draft.visual != null;
    this._edit = { id, draft, saved: JSON.stringify(draft), pending: null, sheet: null };
    this._browse = { visual: draft.visual, soundtrack: draft.soundtrack };
    if (!this._catalog) this._load("catalog");
    this._render();
    this.shadowRoot.querySelector("[data-a=edclose]")?.focus();
  }

  _closeEditor(render = true) {
    if (this._edit) this._flushLive(this._edit);
    this._edit = null;
    if (render) this._render();
  }

  _editSet(patch, render = true) {
    const ed = this._edit;
    Object.assign(ed.draft, patch);
    if (this._snap.ambience?.mode === ed.id) {  // live: the edited mode is playing
      ed.pending = { ...ed.pending, ...patch };
      clearTimeout(ed.timer);
      ed.timer = setTimeout(() => this._flushLive(ed), 200);
    }
    if (render) this._render();
  }

  _flushLive(ed) {
    clearTimeout(ed.timer);
    if (!ed.pending) return;
    this._call("ambience_update", ed.pending);
    ed.pending = null;
  }

  _list(kind) {
    const items = this._catalog?.[kind === "visual" ? "visuals" : "soundtracks"] || [];
    return [...items].sort((x, y) => (y.favorite ? 1 : 0) - (x.favorite ? 1 : 0));  // favourites first
  }

  _cur(kind) {
    const list = this._list(kind);
    const i = Math.max(0, list.findIndex((x) => x.id === this._browse[kind]));
    return { list, i, item: list[i] };
  }

  _browseBy(kind, d) {
    const { list, i } = this._cur(kind);
    if (list.length < 2) return;
    this._browse[kind] = list[(i + d + list.length) % list.length].id;
    this._render();
  }

  _pick(kind) {
    const { item } = this._cur(kind);
    if (!item) return;
    this._browse[kind] = item.id;
    this._editSet(kind === "visual" ? { visual: item.id, visual_enabled: true } : { soundtrack: item.id });
  }

  async _fav(kind) {
    const { item } = this._cur(kind);
    if (!item) return;
    this._browse[kind] = item.id;
    const flip = () => { item.favorite = !item.favorite; this._catVer++; this._render(); };
    flip();
    if (!(await this._call("set_favorite", { kind, media_id: item.id, favorite: item.favorite }))) flip();
  }

  async _refreshCatalog() {
    this._refreshing = true;
    this._render();
    if (await this._call("refresh_catalog")) await this._load("catalog");
    this._refreshing = false;
    this._render();
  }

  async _save() {
    const ed = this._edit;
    const name = this._snap.ambience?.modes?.find((m) => m.id === ed.id)?.name || pretty(ed.id);
    if (!(await this._call("save_mode", { mode: ed.id, ...ed.draft }))) return;
    ed.saved = JSON.stringify(ed.draft);
    this._toast(`Saved ${name}`);
  }

  // every video change saves right away; the list shows the change while it saves and the saved list after
  async _saveVideos(id, rows) {
    if (this._vsave || !this._catalog) return false;
    this._vsave = { id, rows };
    this._render();
    const ok = await this._call("set_mode_videos", { mode: id, videos: rows.map(({ name, url }) => (name ? { name, url } : { url })) });
    if (ok) await this._load("catalog");
    this._vsave = null;
    const ed = this._edit;
    if (ok && ed?.id === id) {  // the saved rotation, so a later Save of the other settings keeps it
      const visuals = [...(this._snap.ambience?.compositions?.[id]?.visuals || [])];
      const patch = { visual: visuals[0] ?? null, visuals, visual_enabled: visuals.length > 0 };
      Object.assign(ed.draft, patch);
      ed.saved = JSON.stringify({ ...JSON.parse(ed.saved), ...patch });
      this._browse.visual = ed.draft.visual;
    }
    this._render();
    return ok;
  }

  async _addVideo() {
    const field = () => this.shadowRoot.querySelector("[data-keep=add]");
    const url = field()?.value.trim();
    if (!url) {
      this._toast("Paste a YouTube link first");
      field()?.focus();
      return;
    }
    if (this._vsave || !this._catalog) return;
    field().value = "";
    const ok = await this._saveVideos(this._edit.id, [...this._rows(this._edit.id), { name: "", url, preview: null, id: null }]);
    if (!ok && field() && !field().value) field().value = url;  // a bad link stays for fixing
  }

  async _removeVideo(i) {
    const id = this._edit.id;
    const rows = [...this._rows(id)];
    const [gone] = rows.splice(i, 1);
    this._edit.sheet = null;
    if (gone && await this._saveVideos(id, rows)) {
      this._toast(`Removed ${gone.name}`, { text: "Undo", action: () => {
        const back = [...this._rows(id)];
        back.splice(i, 0, gone);
        this._saveVideos(id, back);
      } });
    }
  }

  _saveSheet() {
    const ed = this._edit;
    const [name, url] = ["name", "url"].map((f) => this.shadowRoot.querySelector(`[data-sheet=${f}]`)?.value.trim() || "");
    if (!url) {
      this._toast("Enter a YouTube link");
      return;
    }
    const rows = [...this._rows(ed.id)];
    rows[ed.sheet] = { ...rows[ed.sheet], name, url };  // no name: YouTube's title
    ed.sheet = null;
    this._saveVideos(ed.id, rows);
  }

  _sheet(i) {
    this._edit.sheet = i;
    this._render();
    this.shadowRoot.querySelector(i == null ? `[data-a=vedit]` : "[data-sheet=name]")?.focus();
  }

  async _start() {
    const ed = this._edit;
    const saved = JSON.parse(ed.saved);
    // unsaved edits are applied live on top of the saved mode (Save keeps them); only the changed keys, so an
    // untouched visual does not replace the one the mode picked at random
    const changed = Object.fromEntries(Object.entries(ed.draft).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(saved[k])));
    if (await this._call("ambience_start", { mode: ed.id }) && Object.keys(changed).length) {
      this._call("ambience_update", changed);
    }
  }

  _carouselHtml(kind) {
    const label = kind === "visual" ? "Visual" : "Soundtrack";
    if (this._catalogErr && !this._catalog) return `<div class="car-empty">${icon("mdi:alert-circle-outline")}${esc(this._catalogErr)}</div>`;
    if (!this._catalog) return `<div class="skel ${kind}"></div>`;
    const { list, i, item } = this._cur(kind);
    if (!item) return `<div class="car-empty">${icon("mdi:folder-open-outline")}No ${label.toLowerCase()}s yet. Add files and refresh.</div>`;
    const d = this._edit.draft;
    const sel = (kind === "visual" ? d.visual : d.soundtrack) === item.id;
    const meta = [`${i + 1} / ${list.length}`, item.source_type === "youtube" ? "YouTube" : item.live ? "Live" : item.duration ? fmtTime(item.duration) : ""].filter(Boolean).join(" · ");
    const rot = kind === "visual" && (d.visuals || []).includes(item.id);
    const nav = (dir, ic, aria) => `<button class="nav ${dir}" data-a="browse" data-v="${kind}" data-d="${dir === "prev" ? -1 : 1}" aria-label="${aria} ${label.toLowerCase()}"${list.length < 2 ? " disabled" : ""}>${icon(ic)}</button>`;
    return `<div class="car ${kind}${sel ? " sel" : ""}" data-swipe="${kind}" tabindex="0" role="group" aria-roledescription="carousel"
        aria-label="${label}: ${esc(item.title)}, ${i + 1} of ${list.length}${sel ? ", selected" : ""}. Arrow keys browse, Enter selects">
      ${nav("prev", "mdi:chevron-left", "Previous")}
      <button class="slide" data-a="pick" data-v="${kind}" tabindex="-1" aria-pressed="${sel}" aria-label="Use ${esc(item.title)}">
        <span class="art"><span class="ph">${icon(kind === "visual" ? "mdi:image-filter-hdr" : "mdi:music-note")}</span>${img(item.preview)}</span>
        <span class="cap"><b>${esc(item.title)}</b><small>${esc(meta)}</small></span>
        ${sel ? `<span class="check">${icon("mdi:check")}</span>` : ""}
      </button>
      ${kind === "visual" ? `<button class="rot${rot ? " on" : ""}" data-a="rot" aria-pressed="${rot}" aria-label="Random rotation: ${esc(item.title)}">${icon("mdi:shuffle-variant")}</button>` : ""}
      <button class="fav${item.favorite ? " on" : ""}" data-a="fav" data-v="${kind}" aria-pressed="${!!item.favorite}" aria-label="Favourite ${esc(item.title)}">${icon(item.favorite ? "mdi:star" : "mdi:star-outline")}</button>
      ${nav("next", "mdi:chevron-right", "Next")}
    </div>`;
  }

  _videosHtml(rows, playing) {
    if (!this._catalog) return this._catalogErr ? banner(this._catalogErr) : `<div class="skel visual"></div>`;
    const dis = this._vsave ? " disabled" : "";
    const grid = rows.length ? `<ul class="vgrid">${rows.map((v, i) => {
      const name = esc(v.name || "New video");
      return `<li class="tile">
        <div class="thumb${v.id && v.id === playing ? " on" : ""}"><span class="ph">${icon("mdi:youtube")}</span>${img(v.preview)}
          <div class="bar">
            <button class="vb play" data-a="vplay" data-v="${esc(v.id || "")}" aria-label="Play ${name} on the TV"${v.id ? dis : " disabled"}>${icon("mdi:play")}</button>
            <span><button class="vb" data-a="vedit" data-v="${i}" aria-label="Edit ${name}"${dis}>${icon("mdi:pencil")}</button><button class="vb del" data-a="vdel" data-v="${i}" aria-label="Remove ${name}"${dis}>${icon("mdi:close")}</button></span>
          </div>
        </div>
        <span class="cap">${v.name ? name : "Adding…"}</span>
      </li>`;
    }).join("")}</ul>` : `<div class="idle">No videos yet. Paste a YouTube link below.</div>`;
    const s = this._edit.sheet;
    const row = rows[s];
    const field = (key, f, value, label, more) => `<label>${label}<input data-keep="${key}${s}" data-sheet="${f}" type="text" value="${esc(value)}" maxlength="200" autocomplete="off" ${more}></label>`;
    const sheet = row ? `<div class="sheet" role="group" aria-label="Edit video">
        ${field("sn", "name", row.name, "Name", 'placeholder="From YouTube"')}
        ${field("su", "url", row.url, "YouTube link", 'inputmode="url" autocapitalize="off" spellcheck="false"')}
        <div class="acts"><button class="btn sm" data-a="vcancel">Cancel</button><button class="btn sm primary" data-a="vsave"${dis}>Save</button></div>
      </div>` : "";
    return `${grid}${sheet}
      <div class="add">
        <input data-keep="add" type="text" inputmode="url" maxlength="200" autocomplete="off" autocapitalize="off" spellcheck="false"
          placeholder="Paste a YouTube link…" aria-label="YouTube link to add">
        <button class="btn primary" data-a="vadd"${dis}>${icon("mdi:plus")}Add</button>
      </div>`;
  }

  _editorHtml() {
    const a = this._snap.ambience || {};
    const ed = this._edit;
    const d = ed.draft;
    const mode = a.modes.find((m) => m.id === ed.id);
    const live = a.mode === ed.id;
    const rows = this._rows(ed.id);
    const n = rows.length;
    const head = `<div class="edhead">
        <button class="ib flat" data-a="edclose" aria-label="Back to modes">${icon("mdi:arrow-left")}</button>
        <div class="edtitle"><b>${esc(mode.name)}</b><small>${n > 1 ? `One of ${n} videos plays at random, on loop` : n ? "Plays on loop" : "No videos yet"} · saves right away</small></div>
        ${a.backend === "pc" ? `<button class="ib flat${this._refreshing ? " spin" : ""}" data-a="refresh" aria-label="Rescan media">${icon("mdi:refresh")}</button>` : ""}
        ${live
          ? `<button class="btn sm primary" data-a="amb_stop">${icon("mdi:stop")}Stop</button>`
          : `<button class="btn sm primary" data-a="start">${icon("mdi:play")}Start</button>`}
      </div>
      ${this._videosHtml(rows, live ? a.visual : null)}`;
    if (a.backend !== "pc") return head;  // YouTube only: the videos are the whole mode
    const mix = clamp(100 - (Number(d.original_volume) || 0), 0, 100);
    const chip = (act, field, on, ic, label, aria) => `<button class="chip" data-a="${act}" data-v="${field}" aria-pressed="${!!on}"${aria ? ` aria-label="${aria}"` : ""}>${icon(ic)}${label}</button>`;
    return `${head}
      <section class="edsec">
        <h3 class="sh">${icon("mdi:image-outline")}Visual</h3>
        ${this._carouselHtml("visual")}
        <div class="chips">
          ${chip("vis", "visual_enabled", d.visual_enabled, d.visual_enabled ? "mdi:eye-outline" : "mdi:eye-off-outline", "On", "Show visual")}
          ${chip("tog", "video_loop", d.video_loop, "mdi:repeat", "Loop")}
          ${chip("tog", "video_random_start", d.video_random_start, "mdi:shuffle-variant", "Random start")}
        </div>
        ${randomLabel(d) ? hint(`${randomLabel(d)} each time the mode starts`, "mdi:shuffle-variant") : ""}
      </section>
      <section class="edsec">
        <h3 class="sh">${icon("mdi:music-note-outline")}Soundtrack</h3>
        ${this._carouselHtml("soundtrack")}
        <div class="chips">
          ${chip("audio", "soundtrack", d.soundtrack != null, d.soundtrack != null ? "mdi:volume-high" : "mdi:volume-off", "On", "Play soundtrack")}
          ${chip("tog", "soundtrack_loop", d.soundtrack_loop, "mdi:repeat", "Loop")}
          ${chip("tog", "soundtrack_random_start", d.soundtrack_random_start, "mdi:shuffle-variant", "Random start")}
        </div>
        ${d.visual_enabled && this._list("visual").find((x) => x.id === d.visual)?.source_type === "youtube"
          ? hint("A YouTube video plays with its own sound, so the soundtrack and mix are not used") : ""}
      </section>
      <section class="edsec mix">
        <h3 class="sh">${icon("mdi:tune-vertical-variant")}Mix</h3>
        <input type="range" min="0" max="100" step="1" value="${mix}" style="--p:${mix}%" data-mix
          aria-label="Mix between the visual's own sound and the soundtrack" aria-valuetext="${100 - mix}% ambience, ${mix}% soundtrack">
        <div class="mixlab"><span>Ambience <b data-mixa>${100 - mix}%</b></span><span>Soundtrack <b data-mixs>${mix}%</b></span></div>
      </section>
      <div class="actions"><button class="btn" data-a="save">${icon("mdi:content-save-outline")}Save settings</button></div>`;
  }

  // ---------- library tab ----------

  _renderLibrary() {
    this._sec("lib", [this._library, this._libraryErr, this._snap.servers], () => {
      if (this._libraryErr && !this._library) return banner(this._libraryErr);
      if (!this._library) return `<div class="skel shelf-skel"></div><div class="skel shelf-skel"></div>`;
      const out = (this._snap.servers || []).map((srv) => {
        const d = this._library[srv];
        if (!d) return "";
        const rows = this._shelf(srv, "Continue watching", d.continue, true) + this._shelf(srv, "Recently added", d.recent, false);
        return `<section class="srv"><h3 class="sh srvh">${icon(srv === "plex" ? "mdi:plex" : "mdi:jellyfish-outline")}${esc(pretty(srv))}</h3>
          ${rows || `<p class="hint">${icon("mdi:information-outline")}<span>Nothing here yet</span></p>`}</section>`;
      }).join("");
      return out || hint("No library servers responded");
    });
  }

  _shelf(srv, label, items, progress) {
    if (!items?.length) return "";
    return `<h4 class="rowlab">${label}</h4><div class="shelf" role="list">${items.map((it) => {
      const p = progress && it.progress != null ? Math.round(clamp(Number(it.progress) || 0, 0, 1) * 100) : null;
      return `<button class="poster" role="listitem" data-a="play" data-srv="${esc(srv)}" data-v="${esc(it.id)}"
          aria-label="Play ${esc(it.title)}${it.subtitle ? `, ${esc(it.subtitle)}` : ""}${p != null ? `, ${p}% watched` : ""}">
        <span class="art">${icon("mdi:movie-open-outline")}${img(it.image)}${p != null ? `<span class="prog"><span style="width:${p}%"></span></span>` : ""}</span>
        <b>${esc(it.title)}</b><small>${esc(it.subtitle || "")}</small>
      </button>`;
    }).join("")}</div>`;
  }

  // ---------- events ----------

  _onClick(e) {
    const b = e.target.closest?.("[data-a]");
    if (!b || b.disabled) return;
    if (performance.now() < this._noClickUntil) return;  // swallow the click that ends a swipe or long-press
    const v = b.dataset.v;
    const ed = this._edit;
    switch (b.dataset.a) {
      case "tab": return this._setTab(v);
      case "target": return this._setTarget(v);
      case "remote": return this._remote(v);
      case "np": return this._call("remote", { command: v });
      case "touchpad": {
        this._touchpad = !this._touchpad;
        try { localStorage.setItem(PAD_KEY, this._touchpad ? "touchpad" : "dpad"); } catch { /* storage blocked */ }
        return this._render();
      }
      case "click": return this._click(v);
      case "app": return this._call("launch_app", { app: v });
      case "kclear": {
        this.shadowRoot.querySelector("[data-kbdin]").value = "";
        return this._kbd("keyboard_key", { key: "clear" });
      }
      case "ksend": return this._sendTyped(this.shadowRoot.querySelector("[data-kbdin]"));
      case "digit": return this._digit(v);
      case "pc_open": return this._call("pc_open", { client: v });
      case "pc_close": return this._call("pc_close");
      case "drawer": return this._toggleDrawer();
      case "mode": return this._call("ambience_start", { mode: v });
      case "next": return this._call("ambience_start", { mode: this._snap.ambience?.mode, video: v });
      case "vid": return this._call("ambience_start", { mode: b.dataset.m, video: v });
      case "auto": return this._autoAmbience();
      case "amb_stop": return this._call("ambience_stop");
      case "edit": return this._openEditor(v);
      case "edclose": return this._closeEditor();
      case "sleep": return this._call("ambience_sleep_timer", { minutes: Number(v) });
      case "refresh": return this._refreshCatalog();
      case "browse": return this._browseBy(v, Number(b.dataset.d));
      case "pick": return this._pick(v);
      case "fav": return this._fav(v);
      case "rot": {
        const { item } = this._cur("visual");
        if (!item) return undefined;
        this._browse.visual = item.id;
        return this._editSet({ visuals: toggleIn(ed.draft.visuals, item.id) });
      }
      case "tog": return this._editSet({ [v]: !ed.draft[v] });
      case "vis": return this._editSet(ed.draft.visual_enabled ? { visual_enabled: false }
        : { visual_enabled: true, visual: ed.draft.visual ?? this._cur("visual").item?.id ?? null });
      case "audio": return this._editSet({ soundtrack: ed.draft.soundtrack != null ? null : this._cur("soundtrack").item?.id ?? null });
      case "vadd": return this._addVideo();
      case "vdel": return this._removeVideo(Number(v));
      case "vedit": return this._sheet(Number(v));
      case "vcancel": return this._sheet(null);
      case "vsave": return this._saveSheet();
      case "vplay": return this._call("ambience_start", { mode: ed.id, video: v });
      case "save": return this._save();
      case "start": return this._start();
      case "play": {
        this._toast("Starting on the TV…");
        return this._call("play_library_item", { server: b.dataset.srv, item_id: v });
      }
      default: return undefined;
    }
  }

  _onDown(e) {
    if (e.button > 0) return;
    this._noClickUntil = 0;  // a new press is a new gesture: only the click ending a swipe/hold is swallowed
    const tp = e.target.closest?.("[data-touch]");
    if (tp) return this._tpDown(e, tp);
    if (e.target.closest?.("[data-a=kclear],[data-a=ksend]")) e.preventDefault();  // keep the phone keyboard open
    const sw = e.target.closest?.("[data-swipe]");
    if (sw) this._swipe = { x: e.clientX, y: e.clientY, id: e.pointerId, kind: sw.dataset.swipe };
    const h = e.target.closest?.("[data-hold]");
    if (h) this._startHold(h, e);
    return undefined;
  }

  _onMove(e) {
    if (this._pointers.has(e.pointerId)) return this._tpMove(e);
    const h = this._hold;
    if (h && !h.fired && Math.hypot(e.clientX - h.x, e.clientY - h.y) > 10) this._stopHold();
    return undefined;
  }

  _onUp(e) {
    if (this._pointers.has(e.pointerId)) this._tpUp(e);
    const s = this._swipe;
    if (!s || s.id !== e.pointerId) return;
    this._swipe = null;
    const dx = e.clientX - s.x;
    const dy = e.clientY - s.y;
    if (e.type !== "pointerup" || Math.abs(dx) < (s.kind === "target" ? 55 : 38) || Math.abs(dx) <= Math.abs(dy) * 1.15) return;
    this._noClickUntil = performance.now() + 350;
    this._stopHold();
    const dir = dx < 0 ? 1 : -1;
    if (s.kind !== "target") return this._browseBy(s.kind, dir);
    const t = nextTarget(this._eff(), this._snap?.targets, dir);
    if (t === this._eff()) return;
    navigator.vibrate?.(10);
    this._setTarget(t);
  }

  _startHold(el, e) {
    this._stopHold();
    const repeat = el.dataset.hold === "repeat";
    const h = { x: e.clientX, y: e.clientY, fired: false };
    h.timer = setTimeout(() => {
      h.fired = true;
      navigator.vibrate?.(10);
      if (el.dataset.hold === "edit") return this._openEditor(el.dataset.v);
      if (!repeat) {
        this._toast(`Restarting ${el.textContent.trim()}…`);
        this._call("restart_app", { app: el.dataset.v });
        return;
      }
      const fire = () => this._remote(el.dataset.v);
      fire();
      h.iv = setInterval(fire, 150);
    }, repeat ? 400 : 600);
    this._hold = h;
    window.addEventListener("pointerup", this._stopHoldB, { once: true });
    window.addEventListener("pointercancel", this._stopHoldB, { once: true });
  }

  _stopHold() {
    const h = this._hold;
    if (!h) return;
    this._hold = null;
    clearTimeout(h.timer);
    clearInterval(h.iv);
    if (h.fired) this._noClickUntil = performance.now() + 400;
    window.removeEventListener("pointerup", this._stopHoldB);
    window.removeEventListener("pointercancel", this._stopHoldB);
  }

  _onKey(e) {
    const t = e.composedPath()[0];
    if (t.matches?.("[data-kbdin]")) {
      e.stopPropagation();  // keep HA's global shortcuts out of the text field
      if (e.key === "Enter" && !e.isComposing) {
        e.preventDefault();
        this._sendTyped(t);
      } else if (e.key === "Backspace" && !t.value) {
        e.preventDefault();
        this._kbd("keyboard_key", { key: "backspace" });
      }
      return;
    }
    if (t.matches?.("[data-keep]")) {
      e.stopPropagation();  // keep HA's global shortcuts out of the text field
      if (e.key === "Enter" && !e.isComposing) {
        e.preventDefault();
        if (t.dataset.sheet) this._saveSheet();
        else this._addVideo();
      } else if (e.key === "Escape" && t.dataset.sheet) this._sheet(null);
      return;
    }
    if (e.key === "Escape" && this._config.drawer && this._open) {
      this._toggleDrawer(false);
      this.shadowRoot.querySelector(".handle")?.focus();
      return;
    }
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (t.getAttribute?.("role") === "tab" && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
      const tabs = this._tabs();
      e.preventDefault();
      this._setTab(tabs[(tabs.indexOf(this._tab) + step + tabs.length) % tabs.length]);
      this.shadowRoot.querySelector("[role=tab][aria-selected=true]")?.focus();
      return;
    }
    if (step && t.getAttribute?.("role") === "radio") {  // target pills: roving radiogroup
      e.preventDefault();
      this._setTarget(nextTarget(this._eff(), this._snap?.targets, step));
      this.shadowRoot.querySelector("[role=radio][aria-checked=true]")?.focus();
      return;
    }
    if (t.classList?.contains("dpad")) {
      const dir = ARROWS[e.key];
      if (dir || e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        this._remote(dir || "select");
      }
      return;
    }
    const car = t.classList?.contains("car") ? t : null;
    if (car && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
      e.preventDefault();
      this._browseBy(car.dataset.swipe, e.key === "ArrowRight" ? 1 : -1);
      this.shadowRoot.querySelector(`.car.${car.dataset.swipe}`)?.focus();
    } else if (car && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      this._pick(car.dataset.swipe);
      this.shadowRoot.querySelector(`.car.${car.dataset.swipe}`)?.focus();
    }
  }

  _sendTyped(t) {
    const text = t.value.replace(/[\u0000-\u001f\u007f]/g, "");  // eslint-disable-line no-control-regex
    t.value = "";
    if (text) this._kbd("send_text", { text });
  }

  _onInput(e) {
    const t = e.composedPath()[0];
    if (t.matches?.("[data-volume]")) {
      const level = clamp(Math.round(Number(t.value) || 0), 0, 100);
      t.style.setProperty("--p", `${level}%`);
      this._volAt = Date.now();
      clearTimeout(this._volTimer);
      this._volTimer = setTimeout(() => {
        this._volTimer = null;
        this._call("set_volume", { level });
      }, 250);
      return;
    }
    if (!t.matches?.("[data-mix]") || !this._edit) return;
    const v = clamp(Number(t.value) || 0, 0, 100);
    t.style.setProperty("--p", `${v}%`);
    t.setAttribute("aria-valuetext", `${100 - v}% ambience, ${v}% soundtrack`);
    this.shadowRoot.querySelector("[data-mixa]").textContent = `${100 - v}%`;
    this.shadowRoot.querySelector("[data-mixs]").textContent = `${v}%`;
    this._editSet(mixToVolumes(v), false);
    this._slices.ambmain = JSON.stringify(this._ambSlice());  // DOM already shows it; never re-render mid-drag
  }
}

const CSS = `
:host {
  --hh-fg: var(--primary-text-color, #1f2328);
  --hh-fg2: var(--secondary-text-color, #646a73);
  --hh-bg: var(--ha-card-background, var(--card-background-color, #fff));
  --hh-accent: var(--primary-color, #03a9f4);
  --hh-rgb: var(--rgb-primary-color, 3, 169, 244);
  --hh-on-accent: var(--text-primary-color, #fff);
  --hh-line: var(--divider-color, rgba(0, 0, 0, .12));
  --hh-r: var(--ha-card-border-radius, 12px);
  --hh-s1: color-mix(in srgb, var(--hh-fg) 5%, transparent);
  --hh-s2: color-mix(in srgb, var(--hh-fg) 9%, transparent);
  --hh-s3: color-mix(in srgb, var(--hh-fg) 16%, transparent);
  --hh-soft: rgba(var(--hh-rgb), .14);
  /* depth: a neumorphic theme's soft-* tokens raise/sink the controls; other themes keep the flat outline */
  --hh-face: var(--soft-surface, var(--hh-s1));
  --hh-up: var(--soft-shadow-small, inset 0 0 0 1px var(--hh-line));
  --hh-up-lg: var(--soft-shadow-raised, inset 0 0 0 1px var(--hh-line));
  --hh-down: var(--soft-shadow-inset, none);
  --hh-ease: cubic-bezier(.2, .8, .2, 1);
  display: block;
  color: var(--hh-fg);
  -webkit-tap-highlight-color: transparent;
}
*, *::before, *::after { box-sizing: border-box; }
[hidden], [data-s]:empty { display: none !important; }
ha-icon { --mdc-icon-size: 22px; display: inline-flex; flex: none; }
button {
  font: inherit; color: inherit; border: 0; margin: 0; padding: 0; background: none; cursor: pointer;
  touch-action: manipulation; user-select: none; -webkit-user-select: none; -webkit-touch-callout: none;
  transition: background-color .15s, color .15s, box-shadow .2s, transform .12s var(--hh-ease);
}
button:active:not(:disabled) { transform: scale(.96); }
:is(.ib:not(.flat), .btn:not(.primary), .chip, .dk, .rocker button, .key):active:not(:disabled) { box-shadow: var(--hh-down); }
button:disabled { opacity: .38; cursor: default; }
:focus-visible { outline: 2px solid var(--hh-accent); outline-offset: 2px; }
.wrap { container: hh / inline-size; display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; padding: 12px; }
.body, .remote, .col { display: grid; grid-template-columns: minmax(0, 1fr); gap: 18px; min-width: 0; }
.row { display: flex; align-items: center; gap: 8px; }
.row.center { justify-content: center; gap: 10px; }
.ctl { display: grid; gap: 12px; }
.grow { flex: 1; }
h3.sh {
  display: flex; align-items: center; gap: 8px; margin: 0 2px 10px; font-size: 12px; font-weight: 600; letter-spacing: .06em;
  text-transform: uppercase; color: var(--hh-fg2);
}
h3.sh ha-icon { --mdc-icon-size: 16px; }
.sh .note { text-transform: none; letter-spacing: 0; font-size: 13px; }
.sh .chip, .sh .note { margin-left: auto; }
.note { font-weight: 600; color: var(--hh-fg); }
.hint { display: flex; align-items: center; gap: 6px; margin: 6px 4px 0; font-size: 12.5px; color: var(--hh-fg2); }
.hint ha-icon { --mdc-icon-size: 16px; }

/* buttons */
.ib {
  display: inline-grid; place-items: center; min-width: 44px; height: 44px; border-radius: 50%;
  background: var(--hh-face); color: var(--hh-fg); box-shadow: var(--hh-up);
}
.ib.flat { background: none; box-shadow: none; }
.ib.sm { min-width: 36px; height: 36px; }
.ib.sm ha-icon { --mdc-icon-size: 18px; }
.ib.on { background: var(--hh-soft); color: var(--hh-accent); box-shadow: var(--hh-down); }
.ib.accent { background: var(--hh-accent); color: var(--hh-on-accent); box-shadow: 0 4px 12px -4px rgba(var(--hh-rgb), .7); }
.ib.power ha-icon { color: var(--error-color, #db4437); }
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 44px; padding: 0 20px;
  border-radius: 999px; background: var(--hh-face); box-shadow: var(--hh-up); font-weight: 600; font-size: 14px;
}
.btn ha-icon { --mdc-icon-size: 18px; }
.btn.sm { min-height: 36px; padding: 0 14px; gap: 6px; font-size: 13px; white-space: nowrap; }
.btn.primary { background: var(--hh-accent); color: var(--hh-on-accent); box-shadow: 0 4px 14px -6px rgba(var(--hh-rgb), .8); }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chips.center { justify-content: center; }
.chip {
  display: inline-flex; align-items: center; gap: 6px; min-height: 44px; padding: 0 16px; border-radius: 999px;
  background: var(--hh-face); box-shadow: var(--hh-up); font-size: 14px; font-weight: 500;
}
.chip ha-icon { --mdc-icon-size: 18px; color: var(--hh-fg2); }
.chip[aria-pressed=true] { background: var(--hh-soft); box-shadow: var(--soft-shadow-inset, inset 0 0 0 1.5px rgba(var(--hh-rgb), .6)); }
.chip[aria-pressed=true] ha-icon { color: var(--hh-accent); }
.chip.sm { min-height: 36px; padding: 0 12px; font-size: 13px; }
@media (hover: hover) {
  .ib:hover:not(.accent):not(:disabled), .chip:hover:not(:disabled), .btn:not(.primary):hover { background: var(--hh-s2); }
  .ib.flat:hover { background: var(--hh-s1); }
}

/* banner, skeleton */
.banner {
  --c: var(--warning-color, #ffa600);
  display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-radius: 12px; font-size: 13.5px;
  background: color-mix(in srgb, var(--c) 15%, transparent);
}
.banner.err { --c: var(--error-color, #db4437); }
.banner ha-icon { color: var(--c); }
.skel {
  height: 140px; border-radius: 16px; background: linear-gradient(90deg, var(--hh-s1), var(--hh-s2), var(--hh-s1));
  background-size: 200% 100%; animation: hh-shimmer 1.4s linear infinite;
}
.skel.short, .skel.soundtrack { height: 76px; }
.skel.visual { height: auto; aspect-ratio: 16 / 9; max-height: 260px; }
.skel.shelf-skel { height: 200px; }
@keyframes hh-shimmer { to { background-position: -200% 0; } }

/* tabs and target pills: segmented controls */
.tabs, .pills { display: flex; gap: 4px; padding: 4px; border-radius: 999px; background: var(--hh-s1); }
.pills { width: max-content; max-width: 100%; margin: 0 auto; }
.tab, .pill {
  flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-width: 0; min-height: 44px; padding: 0 14px;
  border-radius: 999px; color: var(--hh-fg2); font-weight: 600; font-size: 14px; white-space: nowrap;
}
.tab span, .pill span { overflow: hidden; text-overflow: ellipsis; }
.pill { flex: 0 1 auto; }
.tab ha-icon, .pill ha-icon { --mdc-icon-size: 20px; }
.tab[aria-selected=true], .pill[aria-checked=true] {
  background: var(--hh-bg); color: var(--hh-fg); box-shadow: 0 1px 3px rgba(0, 0, 0, .18), inset 0 0 0 1px var(--hh-line);
}
.tab[aria-selected=true] ha-icon, .pill[aria-checked=true] ha-icon { color: var(--hh-accent); }
@container hh (max-width: 380px) { .tab ha-icon { display: none; } }

/* now playing */
.np {
  position: relative; display: flex; align-items: center; gap: 12px; padding: 10px; border-radius: 16px;
  overflow: hidden; isolation: isolate; background: var(--hh-s1); box-shadow: inset 0 0 0 1px var(--hh-line);
}
.npbg { position: absolute; inset: -24px; width: calc(100% + 48px); height: calc(100% + 48px); object-fit: cover; filter: blur(28px) saturate(1.5); opacity: .28; z-index: -1; }
.npart { position: relative; flex: none; display: grid; place-items: center; width: 52px; height: 72px; border-radius: 10px; overflow: hidden; background: var(--hh-s2); color: var(--hh-fg2); }
.npart img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.npmeta { flex: 1; min-width: 0; }
.npmeta b, .npmeta small { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.npmeta b { font-size: 15px; font-weight: 600; }
.npmeta small { font-size: 12.5px; color: var(--hh-fg2); margin-top: 2px; }
.npbar { height: 4px; margin-top: 10px; border-radius: 2px; background: var(--hh-s3); overflow: hidden; }
.npfill { display: block; height: 100%; width: 0; border-radius: inherit; background: var(--hh-accent); transition: width 1s linear; }
.nptime { display: flex; justify-content: space-between; margin-top: 4px; font-size: 11px; color: var(--hh-fg2); font-variant-numeric: tabular-nums; }
.npbtns { display: flex; align-items: center; gap: 2px; flex: none; }
@container hh (max-width: 380px) {
  .np { flex-wrap: wrap; }
  .npmeta { flex-basis: calc(100% - 64px); }
  .npbtns { width: 100%; justify-content: center; gap: 16px; }
}

/* remote */
.remote { gap: 20px; }
.col { gap: 14px; align-content: start; }
@container hh (min-width: 720px) {
  .remote { grid-template-columns: minmax(300px, 400px) minmax(0, 1fr); gap: 28px; }
}
.pad { display: grid; justify-items: center; gap: 10px; }
.dpad {
  display: grid; grid-template: ". u ." 1fr "l o r" 1fr ". d ." 1fr / 1fr 1fr 1fr;
  width: 260px; width: min(260px, 76cqi); aspect-ratio: 1; border-radius: 50%; padding: 6px;
  background: var(--hh-face); box-shadow: var(--hh-up-lg); touch-action: pan-y;
}
.dk { display: grid; place-items: center; width: 100%; height: 100%; border-radius: 50%; color: var(--hh-fg2); }
.dk ha-icon { --mdc-icon-size: 30px; }
.dk:active { background: var(--hh-s2); color: var(--hh-fg); }
.dk.up { grid-area: u; } .dk.down { grid-area: d; } .dk.left { grid-area: l; } .dk.right { grid-area: r; }
.ok {
  grid-area: o; place-self: center; width: 96%; aspect-ratio: 1; border-radius: 50%;
  background: var(--hh-accent); color: var(--hh-on-accent); font-size: 17px; font-weight: 700; letter-spacing: .06em;
  box-shadow: var(--soft-shadow-small, 0 8px 20px -8px rgba(var(--hh-rgb), .9)), inset 0 1px 0 rgba(255, 255, 255, .25);
}
.ok:active:not(:disabled) { box-shadow: inset 3px 3px 6px rgba(0, 0, 0, .35), inset -3px -3px 6px rgba(255, 255, 255, .3); }
.dpad:focus-visible { outline-offset: 4px; }
.touch {
  display: grid; place-content: center; justify-items: center; gap: 8px; width: 100%; max-width: 380px; height: 200px;
  border-radius: 20px; background: var(--hh-face); box-shadow: var(--soft-shadow-inset, inset 0 0 0 1px var(--hh-line));
  color: var(--hh-fg2); font-size: 13px; text-align: center; padding: 16px; touch-action: none; user-select: none; -webkit-user-select: none;
}
.touch ha-icon { --mdc-icon-size: 32px; opacity: .7; }
.clicks { display: flex; gap: 8px; width: 100%; max-width: 380px; }
.clicks .btn { flex: 1; }
.rocker { display: inline-flex; align-items: center; height: 48px; border-radius: 999px; background: var(--hh-face); box-shadow: var(--hh-up); }
.rocker button { display: inline-flex; align-items: center; justify-content: center; gap: 4px; min-width: 48px; height: 48px; border-radius: 999px; }
.rocker .mute { min-width: 72px; font-weight: 600; font-variant-numeric: tabular-nums; }
.rocker .mute ha-icon { --mdc-icon-size: 20px; color: var(--hh-fg2); }
.rocker .mute.on ha-icon { color: var(--error-color, #db4437); }
.rocker button:active { background: var(--hh-s2); }
.apps { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
.app {
  position: relative; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 5px; aspect-ratio: 1.3;
  min-width: 0; padding: 4px; border-radius: 12px; overflow: hidden; background: var(--b, var(--hh-s2)); color: var(--f, var(--hh-accent));
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, .06), 0 1px 3px rgba(0, 0, 0, .18);
}
.app svg { width: 26px; height: 26px; fill: currentColor; flex: none; }
.app ha-icon { --mdc-icon-size: 26px; }
.app .cap { max-width: 100%; font-size: 11px; font-weight: 600; line-height: 1.1; opacity: .9; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.app .wm { font-size: 16px; font-weight: 800; line-height: 1.1; text-align: center; }
.app::before { content: ""; position: absolute; inset: 0; background: linear-gradient(180deg, rgba(255, 255, 255, .14), transparent 60%); pointer-events: none; }
.app.on { box-shadow: 0 0 0 2px var(--hh-bg), 0 0 0 4px var(--hh-accent), 0 10px 24px -8px rgba(var(--hh-rgb), .9); }
.kbdwrap { display: grid; grid-template-columns: minmax(0, 1fr); }
.kbd {
  display: flex; align-items: center; gap: 4px; height: 48px; padding: 0 2px 0 14px; border-radius: 999px;
  background: var(--hh-s1); box-shadow: inset 0 0 0 1px var(--hh-line); transition: box-shadow .15s, background-color .15s;
}
.kbd:focus-within { background: var(--hh-bg); box-shadow: inset 0 0 0 2px var(--hh-accent); }
.kbd > ha-icon { color: var(--hh-fg2); }
.kbd input { flex: 1; min-width: 0; height: 100%; border: 0; outline: 0; background: none; color: inherit; font: inherit; font-size: 16px; }
.kbd input::placeholder { color: var(--hh-fg2); opacity: 1; }
.kbd .ib { background: none; }
.kbd .btn { margin-right: 4px; }
.chd { margin-left: auto; min-width: 4ch; font-size: 22px; font-weight: 600; color: var(--hh-fg); font-variant-numeric: tabular-nums; text-align: right; }
.keypad { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; max-width: 320px; margin: 0 auto 12px; }
.key { min-height: 52px; border-radius: 14px; background: var(--hh-face); box-shadow: var(--hh-up); font-size: 20px; font-weight: 500; display: grid; place-items: center; }
.key:active { background: var(--hh-s2); }
.key.go { background: var(--hh-soft); color: var(--hh-accent); }

/* ambience */
.ambmain { display: grid; gap: 12px; min-width: 0; }
.ambmain h3.sh { margin-bottom: 0; }
.ph { position: absolute; inset: 0; display: grid; place-items: center; color: var(--hh-fg2); background: radial-gradient(circle at 30% 20%, rgba(var(--hh-rgb), .28), transparent 65%), var(--hh-s2); }
.ph ha-icon { --mdc-icon-size: 40px; opacity: .7; }
/* artwork is dark in either theme, so the white text on it is too */
.hero .ph, .tva .ph { color: rgba(255, 255, 255, .7); background: radial-gradient(circle at 30% 20%, rgba(var(--hh-rgb), .5), transparent 70%), #1e252e; }
.hero img, .tva img, .thumb img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.hero {
  --fade: var(--soft-surface, var(--hh-bg));
  position: relative; aspect-ratio: 3 / 4; max-height: 560px; width: calc(100% + 24px); margin: -8px -12px 0; border-radius: 26px 26px 0 0;
  overflow: hidden; background: var(--fade); color: #fff; isolation: isolate;
}
.hero img { animation: hh-kb 24s ease-in-out infinite alternate; }
@keyframes hh-kb { to { transform: scale(1.08) translate(-1.5%, -1%); } }
@media (prefers-reduced-motion: reduce) { .hero img { animation: none; } }
.hero .over {
  position: absolute; inset: 0; display: flex; flex-direction: column; justify-content: flex-end; align-items: flex-start; gap: 4px; padding: 22px 22px 70px;
  background: linear-gradient(rgba(0, 0, 0, .25), transparent 22%, transparent 40%, rgba(0, 0, 0, .55) 66%, color-mix(in srgb, var(--fade) 92%, #000) 92%, var(--fade));
}
.eyebrow { font-size: 11.5px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; opacity: .85; margin-bottom: 2px; }
.live { display: inline-flex; align-items: center; gap: 6px; }
.live::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: #ff5a4e; box-shadow: 0 0 0 3px rgba(255, 90, 78, .3); }
.hn, .hero small { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hn { font-size: 40px; font-weight: 800; letter-spacing: -.02em; line-height: 1.02; text-shadow: 0 2px 16px rgba(0, 0, 0, .45); max-width: 100%; }
.hero small { font-size: 14px; opacity: .85; max-width: 100%; }
.acts { display: flex; align-items: center; gap: 12px; margin-top: 16px; }
.hb {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 44px; padding: 0 22px; border-radius: 999px; white-space: nowrap;
  background: rgba(255, 255, 255, .18); color: #fff; font-size: 15px; font-weight: 700; backdrop-filter: blur(14px) saturate(1.4); -webkit-backdrop-filter: blur(14px) saturate(1.4);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, .14);
}
.hb ha-icon { --mdc-icon-size: 20px; }
.hb.primary { background: #fff; color: #111; min-width: 132px; box-shadow: 0 8px 24px -8px rgba(0, 0, 0, .7); }
.hb.round { width: 44px; padding: 0; }
@media (hover: hover) { .hb:not(.primary):hover { background: rgba(255, 255, 255, .3); } }
[data-s=hero]:not(:empty) + [data-s=sleep], [data-s=hero]:not(:empty) + [data-s=sleep]:empty + .ambmain { margin-top: -58px; position: relative; z-index: 1; }
.ambmain > h3.sh, .sleep h3.sh { font-size: 19px; font-weight: 700; letter-spacing: -.01em; text-transform: none; color: var(--hh-fg); margin-bottom: 8px; }
/* Apple TV rows: posters that scroll sideways and snap, title under the art */
.tvrow {
  display: grid; grid-auto-flow: column; grid-auto-columns: 64%; gap: 14px; overflow-x: auto; overscroll-behavior-x: contain;
  scroll-snap-type: x mandatory; scroll-padding: 0 4px; padding: 6px 4px 12px; margin: -6px -4px -8px; scrollbar-width: none; touch-action: pan-x pan-y;
}
.tvrow::-webkit-scrollbar { display: none; }
.tvrow.sm { grid-auto-columns: 46%; }
.tvrow { padding: 10px 10px 14px; margin: -10px -10px -8px; scroll-padding: 0 10px; }  /* room for the lifted tile's scale and glow */
.tvw { position: relative; min-width: 0; scroll-snap-align: start; }
.tvw .tvp { width: 100%; }
.tvedit {
  position: absolute; top: 8px; right: 8px; display: grid; place-items: center; width: 34px; height: 34px; border-radius: 50%; color: #fff;
  background: rgba(0, 0, 0, .45); backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); box-shadow: inset 0 0 0 1px rgba(255, 255, 255, .18);
}
.tvedit ha-icon { --mdc-icon-size: 17px; }
.tvp.on ~ .tvedit { transform: translate(4px, -4px); }  /* follows the lifted tile */
.tvp { display: grid; gap: 3px; min-width: 0; text-align: left; color: var(--hh-fg); scroll-snap-align: start; }
.tvp:active:not(:disabled) { transform: scale(.97); }
.tva {
  position: relative; display: block; aspect-ratio: 16 / 9; border-radius: 14px; overflow: hidden; background: #1e252e; margin-bottom: 5px;
  box-shadow: 0 10px 22px -12px rgba(0, 0, 0, .7); transition: transform .25s var(--hh-ease), box-shadow .25s;
}
.tvp.on .tva { transform: scale(1.04); box-shadow: 0 0 0 2px rgba(255, 255, 255, .9), 0 14px 30px -10px rgba(var(--hh-rgb), .8); }
@media (hover: hover) { .tvp:hover .tva { transform: translateY(-3px) scale(1.02); } }
.tvp b, .tvp small { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; padding: 0 2px; }
.tvp b { font-size: 14.5px; font-weight: 600; }
.tvp small { font-size: 12.5px; color: var(--hh-fg2); }
.tvp.on small { color: var(--hh-accent); font-weight: 600; }
.tvrow.sm .tvp b { font-size: 13px; font-weight: 500; color: var(--hh-fg2); }
.tvrow.sm .tvp.on b { color: var(--hh-fg); }
.eq { position: absolute; right: 8px; bottom: 8px; display: flex; align-items: flex-end; gap: 2px; height: 16px; padding: 4px 5px; border-radius: 6px; background: rgba(0, 0, 0, .55); box-sizing: content-box; }
.eq i { width: 3px; background: #fff; border-radius: 1px; animation: hh-eq 1s ease-in-out infinite alternate; }
.eq i:nth-child(2) { animation-delay: -.4s; } .eq i:nth-child(3) { animation-delay: -.7s; }
@keyframes hh-eq { from { height: 25%; } to { height: 100%; } }
@media (prefers-reduced-motion: reduce) { .eq i { animation: none; height: 70%; } }
.idle {
  display: flex; align-items: center; justify-content: center; gap: 8px; padding: 16px; border: 1px dashed var(--hh-line);
  border-radius: 14px; color: var(--hh-fg2); font-size: 13.5px; text-align: center; text-wrap: balance;
}
.sleep h3.sh { margin-bottom: 6px; min-height: 36px; }
.sleep .ib { margin-left: -4px; }
.slchips { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px; }
.slchips .chip { justify-content: center; padding: 0 6px; }
.vb { display: grid; place-items: center; border-radius: 50%; background: rgba(8, 12, 18, .66); color: #fff; }
.vb ha-icon { --mdc-icon-size: 17px; }
.auto { display: flex; align-items: center; gap: 12px; padding: 12px 14px; border-radius: 16px; background: var(--hh-face); box-shadow: var(--hh-up); }
.ax { flex: 1; min-width: 0; }
.ax b { display: block; font-size: 14px; font-weight: 600; }
.ax b span { font-weight: 400; color: var(--hh-fg2); }
.ax small { display: block; font-size: 12px; color: var(--hh-fg2); }
.sw { position: relative; flex: none; width: 46px; height: 28px; border-radius: 999px; background: var(--hh-s3); }
.sw::after {
  content: ""; position: absolute; top: 3px; left: 3px; width: 22px; height: 22px; border-radius: 50%; background: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, .3); transition: transform .2s var(--hh-ease);
}
.sw[aria-checked=true] { background: var(--hh-accent); }
.sw[aria-checked=true]::after { transform: translateX(18px); }
.sw:active:not(:disabled) { transform: none; }
.edhead { display: flex; align-items: center; gap: 8px; }
.edtitle { flex: 1; min-width: 0; }
.edtitle b { display: block; font-size: 17px; font-weight: 600; }
.edtitle small { display: block; font-size: 12px; line-height: 1.35; color: var(--hh-fg2); }
.spin ha-icon { animation: hh-spin 1s linear infinite; }
@keyframes hh-spin { to { transform: rotate(360deg); } }
.edsec { display: grid; gap: 10px; margin-top: 16px; }
.edsec h3.sh { margin-bottom: 0; }
.car { position: relative; border-radius: 16px; overflow: hidden; background: var(--hh-s1); touch-action: pan-y; }
.car::after { content: ""; position: absolute; inset: 0; border-radius: inherit; pointer-events: none; transition: box-shadow .2s; }
.car.sel::after { box-shadow: inset 0 0 0 3px var(--hh-accent); }
.slide { display: block; width: 100%; height: 100%; text-align: left; }
.slide:active { transform: none; }
.art { position: relative; display: block; overflow: hidden; }
.art img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.cap b, .cap small { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cap b { font-size: 15px; font-weight: 600; }
.cap small { font-size: 12px; margin-top: 2px; font-variant-numeric: tabular-nums; }
.check { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 50%; background: var(--hh-accent); color: var(--hh-on-accent); }
.check ha-icon { --mdc-icon-size: 18px; }
.nav, .fav, .rot { display: grid; place-items: center; width: 44px; height: 44px; border-radius: 50%; }
.fav.on ha-icon { color: var(--warning-color, #ffc107); }
.rot.on ha-icon { color: var(--hh-accent); }
/* visual: big 16:9 slide with glass controls on top */
.car.visual { aspect-ratio: 16 / 9; max-height: 260px; }
.car.visual .slide { position: absolute; inset: 0; }
.car.visual .art { position: absolute; inset: 0; }
.car.visual .cap { position: absolute; left: 0; right: 0; bottom: 0; padding: 32px 56px 12px 16px; color: #fff; background: linear-gradient(transparent, rgba(0, 0, 0, .7)); }
.car.visual .cap small { opacity: .85; }
.car.visual .check { position: absolute; top: 10px; left: 10px; }
.car.visual .nav, .car.visual .fav, .car.visual .rot { position: absolute; z-index: 1; color: #fff; }
.car.visual .nav::before, .car.visual .fav::before, .car.visual .rot::before { content: ""; position: absolute; inset: 6px; border-radius: 50%; background: rgba(0, 0, 0, .38); backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px); }
.car.visual .nav ha-icon, .car.visual .fav ha-icon, .car.visual .rot ha-icon { position: relative; }
.car.visual .nav { top: 50%; margin-top: -22px; }
.car.visual .prev { left: 4px; } .car.visual .next { right: 4px; }
.car.visual .fav { top: 4px; right: 4px; }
.car.visual .rot { top: 4px; right: 48px; }
.volrow { display: flex; align-items: center; gap: 10px; }
.volrow input { flex: 1; min-width: 0; }
.volrow output { min-width: 3ch; text-align: right; color: var(--hh-fg2); font-variant-numeric: tabular-nums; }
.ib.mute.on ha-icon { color: var(--error-color, #db4437); }
/* soundtrack: compact row */
.car.soundtrack { display: flex; align-items: center; gap: 2px; padding: 8px 4px; }
.car.soundtrack .slide { flex: 1; min-width: 0; display: flex; align-items: center; gap: 12px; padding: 0 4px; }
.car.soundtrack .art { width: 56px; height: 56px; flex: none; border-radius: 12px; }
.car.soundtrack .ph ha-icon { --mdc-icon-size: 28px; }
.car.soundtrack .cap { flex: 1; min-width: 0; }
.car.soundtrack .cap small { color: var(--hh-fg2); }
.car.soundtrack .nav, .car.soundtrack .fav { flex: none; color: var(--hh-fg2); }
.car-empty { display: flex; align-items: center; gap: 10px; padding: 18px 16px; border-radius: 16px; background: var(--hh-s1); color: var(--hh-fg2); font-size: 13.5px; }
input[type=range] {
  --p: 50%; -webkit-appearance: none; appearance: none; width: 100%; height: 44px; margin: 0; background: transparent; cursor: pointer;
}
input[type=range]::-webkit-slider-runnable-track { height: 10px; border-radius: 5px; background: linear-gradient(90deg, var(--hh-accent) var(--p), var(--soft-recess, var(--hh-s3)) var(--p)); box-shadow: var(--hh-down); }
input[type=range]::-moz-range-track { height: 10px; border-radius: 5px; background: linear-gradient(90deg, var(--hh-accent) var(--p), var(--soft-recess, var(--hh-s3)) var(--p)); box-shadow: var(--hh-down); }
input[type=range]::-webkit-slider-thumb {
  -webkit-appearance: none; width: 26px; height: 26px; margin-top: -8px; border-radius: 50%; border: 0;
  background: var(--hh-face); box-shadow: 0 0 0 2px var(--hh-accent), var(--soft-shadow-small, 0 2px 8px rgba(0, 0, 0, .3));
}
input[type=range]::-moz-range-thumb { width: 26px; height: 26px; border-radius: 50%; border: 0; background: var(--hh-face); box-shadow: 0 0 0 2px var(--hh-accent), var(--soft-shadow-small, 0 2px 8px rgba(0, 0, 0, .3)); }
input[type=range]:focus-visible { outline: none; }
input[type=range]:focus-visible::-webkit-slider-thumb { box-shadow: 0 0 0 2px var(--hh-accent), 0 0 0 6px rgba(var(--hh-rgb), .3); }
.mixlab { display: flex; justify-content: space-between; margin-top: -4px; font-size: 12.5px; color: var(--hh-fg2); }
.mixlab b { color: var(--hh-fg); font-weight: 600; font-variant-numeric: tabular-nums; }
.vgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; margin: 0; padding: 0; list-style: none; }
.tile { display: grid; gap: 5px; min-width: 0; }
.thumb { position: relative; aspect-ratio: 16 / 9; border-radius: 10px; overflow: hidden; background: var(--hh-s2); }
.thumb .ph ha-icon { --mdc-icon-size: 28px; }
.thumb.on::after { content: ""; position: absolute; inset: 0; border-radius: inherit; box-shadow: inset 0 0 0 3px var(--hh-accent); pointer-events: none; }
.bar { position: absolute; left: 0; right: 0; bottom: 0; display: flex; justify-content: space-between; padding: 4px; background: linear-gradient(transparent, rgba(0, 0, 0, .7)); }
.bar span { display: flex; gap: 4px; }
.vb { width: 34px; height: 34px; }
.vb.play { background: var(--hh-accent); color: var(--hh-on-accent); }
@media (hover: hover) { .vb.del:hover { background: var(--error-color, #db4437); } }
.tile .cap { font-size: 12.5px; line-height: 1.3; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.add { display: flex; gap: 8px; }
.add input, .sheet input {
  flex: 1; width: 100%; min-width: 0; height: 44px; padding: 0 12px; border: 0; border-radius: 10px; background: var(--hh-s1); color: inherit;
  box-shadow: inset 0 0 0 1px var(--hh-line); font: inherit; font-size: 16px; outline: 0;
}
.add input:focus-visible, .sheet input:focus-visible { background: var(--hh-bg); box-shadow: inset 0 0 0 2px var(--hh-accent); }
.add input::placeholder, .sheet input::placeholder { color: var(--hh-fg2); opacity: 1; }
.add .btn { padding: 0 16px; }
.sheet { display: grid; gap: 8px; padding: 12px; border-radius: 12px; background: var(--hh-s1); }
.sheet label { display: grid; gap: 4px; font-size: 12px; color: var(--hh-fg2); }
.sheet input { background: var(--hh-bg); }
.sheet .acts { justify-content: flex-end; margin: 0; }
.actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
.actions .btn { min-width: 112px; }

/* library */
.lib { display: grid; gap: 18px; }
.srv { min-width: 0; }
.srvh { color: var(--hh-fg) !important; font-size: 15px !important; }
.rowlab { margin: 4px 2px 8px; font-size: 13px; font-weight: 600; color: var(--hh-fg2); }
.shelf {
  display: grid; grid-auto-flow: column; grid-auto-columns: 116px; gap: 12px; overflow-x: auto; padding: 2px 2px 10px;
  margin-bottom: 6px; scroll-snap-type: x mandatory; overscroll-behavior-x: contain; scrollbar-width: thin;
}
.poster { display: grid; align-content: start; gap: 6px; min-width: 0; text-align: left; scroll-snap-align: start; }
.poster .art { display: grid; place-items: center; aspect-ratio: 2 / 3; border-radius: 12px; background: var(--hh-s2); color: var(--hh-fg2); box-shadow: 0 2px 8px rgba(0, 0, 0, .16); }
.poster b { font-size: 13px; font-weight: 600; line-height: 1.25; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.poster small { font-size: 12px; color: var(--hh-fg2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: -4px; }
.prog { position: absolute; left: 8px; right: 8px; bottom: 8px; height: 4px; border-radius: 2px; background: rgba(255, 255, 255, .38); overflow: hidden; }
.prog > span { display: block; height: 100%; background: var(--hh-accent); }
@media (hover: hover) { .poster:hover .art { box-shadow: 0 0 0 2px var(--hh-accent), 0 6px 16px rgba(0, 0, 0, .2); } }

/* drawer: fixed to the right edge, only the handle shows when closed */
:host([drawer]) { position: fixed; top: 50%; right: env(safe-area-inset-right, 0px); z-index: 5; transform: translateY(-50%); pointer-events: none; }
.drawer {
  --pw: min(380px, calc(100vw - 46px));
  display: flex; align-items: center; transform: translateX(var(--pw)); transition: transform .2s var(--hh-ease);
}
.drawer[data-open=true] { transform: none; }
.drawer > * { pointer-events: auto; }
.handle {
  position: relative; display: grid; place-items: center; flex: none; width: 28px; height: 72px; border-radius: 14px 0 0 14px;
  background: var(--hh-bg); color: var(--hh-fg2); box-shadow: -4px 0 16px rgba(0, 0, 0, .18), inset 1px 0 0 var(--hh-line);
}
.handle::before { content: ""; position: absolute; inset: -12px 0 -12px -14px; }
.handle:active { transform: none; }
.handle ha-icon { transition: transform .2s var(--hh-ease); }
.drawer[data-open=true] .handle ha-icon { transform: rotate(180deg); }
.panel {
  width: var(--pw); max-height: min(88vh, 820px); overflow-y: auto; overscroll-behavior: contain;
  border-radius: var(--hh-r) 0 0 var(--hh-r); box-shadow: -10px 0 32px rgba(0, 0, 0, .22);
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition: none !important; animation: none !important; }
}
`;

// ponytail: HA swaps window.customElements for a new registry ~70 ms into page load (seen on 2026.9); this extra
// module can run before that and define into the old one, so define again until the current registry has it.
// A subclass, because the new registry may forward to the old one, where the class is already taken.
const define = () => {
  if (customElements.get(TAG)) return;
  try { customElements.define(TAG, HomeHubMediaCard); } catch { customElements.define(TAG, class extends HomeHubMediaCard {}); }
};
define();
for (const ms of [100, 500, 2000, 5000]) setTimeout(define, ms);
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === TAG)) {
  window.customCards.push({ type: TAG, name: "HomeHub Media", description: "Remote, ambience and library", preview: true });
}
if (window.__HOMEHUB_TEST__) window.__homehubTest = { esc, mixToVolumes, nextTarget, textRoute, canSeek, toggleIn, randomLabel, volumeSlider, modeIds, nextId, modeVideos, featuredMode };
