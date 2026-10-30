/// <reference types="vite/client" />
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  /** Older alias of VITE_API_BASE_URL. */
  readonly VITE_API_BASE?: string;
  readonly VITE_MAP_STYLE_URL?: string;
  readonly VITE_LOCATION_API_KEY?: string;
  readonly VITE_LOCATION_REGION?: string;
  readonly VITE_LOCATION_STYLE?: string;
  readonly VITE_LOCATION_COLOR_SCHEME?: string;
  readonly VITE_LOCATION_POLITICAL_VIEW?: string;
}
interface ImportMeta { readonly env: ImportMetaEnv }
