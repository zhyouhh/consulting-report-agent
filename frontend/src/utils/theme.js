export const THEME_KEY = 'cra:theme'
export function normalizeTheme(v){ return v === 'dark' ? 'dark' : 'light' }
export function nextTheme(t){ return normalizeTheme(t) === 'dark' ? 'light' : 'dark' }
export function getInitialTheme(){
  try { return normalizeTheme(localStorage.getItem(THEME_KEY)) } catch { return 'light' }
}
export function applyTheme(t){
  const theme = normalizeTheme(t)
  const root = document.documentElement
  if (theme === 'dark') root.classList.add('dark'); else root.classList.remove('dark')
  return theme
}
export function toggleTheme(cur){
  const t = nextTheme(cur)
  try { localStorage.setItem(THEME_KEY, t) } catch { /* ignore */ }
  applyTheme(t)
  return t
}
