/** @type {import('tailwindcss').Config} */
const c = (v) => `rgb(var(${v}) / <alpha-value>)`
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        bg:c('--bg'), chat:c('--chat'), ws:c('--ws'),
        card:c('--card'), card2:c('--card2'), field:c('--field'),
        border:c('--border'), col:c('--col'), hair:c('--hair'), track:c('--track'),
        text:c('--text'), t2:c('--t2'), t3:c('--t3'),
        accent:c('--accent'), abright:c('--abright'),
        asoft:c('--asoft'), asoftb:c('--asoftb'), asoftt:c('--asoftt'),
        sel:c('--sel'), userbub:c('--userbub'), stepdone:c('--stepdone'), dotfuture:c('--dotfuture'),
        scrim:c('--scrim'), success:c('--success'), warn:c('--warn'), error:c('--error'),
      },
      fontFamily: {
        sans:['Hanken Grotesk','PingFang SC','Microsoft YaHei','Noto Sans SC','system-ui','sans-serif'],
        mono:['IBM Plex Mono','monospace'],
      },
      borderRadius: { chip:'5px', tag:'6px', ibtn:'7px', btn:'8px', card:'11px', win:'14px' },
      boxShadow: {
        card:'0 1px 2px rgba(0,0,0,.04)',
        popover:'0 24px 60px rgba(0,0,0,.3)',
        float:'0 24px 70px rgba(0,0,0,.45)',
      },
      fontSize: {
        '2xs':'10.5px','11':'11px','xs':'11.5px','12':'12px','13':'12.5px',
        sm:'13px','15':'13.5px','base':'15px','lg':'17px','xl':'18px',
      },
    },
  },
  plugins: [],
}
