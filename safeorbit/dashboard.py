"""P2/P3 单页管理台：登录、轨迹审计、策略中心、处置中心、代码审计。"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SafeOrbit · 审计与治理台</title>
<link rel="icon" type="image/svg+xml" href="/favicon.svg?v=3">
<meta name="theme-color" content="#08090a">
<style>
  :root {
    color-scheme: dark;
    --bg: #101315; --card: #191e22; --ink: #e6ecef; --muted: #9ba8b1;
    --line: #2d363d; --green: #60d3aa; --red: #ff8585; --amber: #edc279;
    --blue: #65d6c1; --purple: #b4a5e8; --surface: #20272c; --accent-bg: #193630;
  }
  * { box-sizing: border-box; }
  [hidden] { display: none !important; }
  body { margin: 0; background: var(--bg); color: var(--ink);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    font-size: 15px; line-height: 1.65; overflow-wrap: anywhere; }
  button, input, select, textarea { font: inherit; }
  a { color: var(--blue); text-underline-offset: 4px; }
  :focus-visible { outline: 2px solid var(--blue); outline-offset: 4px; }
  .page { position: relative; z-index: 1; display: grid; grid-template-columns: 232px minmax(0, 1fr); min-height: 100vh; }
  /* 动态审计底纹：网格漂移 + 扫描光束 + 缓慢呼吸的光晕，弱对比不抢正文 */
  .bg-ambient { position: fixed; inset: 0; z-index: 0; overflow: hidden; pointer-events: none; }
  .bg-ambient::before { content: ""; position: absolute; inset: -80px;
    background-image:
      linear-gradient(to right, #4ed3b80a 1px, transparent 1px),
      linear-gradient(to bottom, #4ed3b80a 1px, transparent 1px);
    background-size: 56px 56px;
    -webkit-mask-image: radial-gradient(ellipse 82% 74% at 62% 26%, #000 16%, transparent 84%);
    mask-image: radial-gradient(ellipse 82% 74% at 62% 26%, #000 16%, transparent 84%); }
  .bg-halo { position: absolute; width: 760px; height: 760px; right: -200px; top: -280px;
    background: radial-gradient(circle, #4ed3b812 0%, #4ed3b807 40%, transparent 68%); }
  .bg-scan { position: absolute; left: 0; right: 0; height: 220px; top: 0;
    background: linear-gradient(to bottom, transparent, #4ed3b808 32%, #8ff0dc1c 50%, #4ed3b808 68%, transparent); }
  .sidebar { position: sticky; top: 0; height: 100vh; overflow-y: auto; padding: 32px 18px 24px;
    background: #14191c; border-right: 1px solid var(--line); display: flex; flex-direction: column; }
  .brand { font-size: 25px; font-weight: 750; letter-spacing: -.8px; padding: 0 14px; line-height: 1.3; }
  .brand span { color: var(--blue); }
  .brand small { display: block; margin-top: 8px; color: var(--muted); font-size: 13px;
    letter-spacing: 2px; font-weight: 500; }
  .nav-caption { margin: 48px 14px 12px; color: #87959e; font-size: 12px; letter-spacing: 2px; }
  .tabs { display: flex; flex-direction: column; gap: 7px; }
  .tab { display: flex; align-items: center; gap: 12px; text-align: left; border: 1px solid transparent;
    background: transparent; color: var(--muted); padding: 12px 14px; border-radius: 8px; font-weight: 500; }
  .tab::before { content: "01"; font: 13px Consolas, monospace; opacity: .65; }
  .tab:nth-child(2)::before { content: "02"; } .tab:nth-child(3)::before { content: "03"; }
  .tab:nth-child(4)::before { content: "04"; } .tab:nth-child(5)::before { content: "05"; }
  .tab.active { background: var(--accent-bg); border-color: #2c5149; color: #90e3d2;
    box-shadow: inset 3px 0 0 var(--blue); }
  .sidebar-foot { margin-top: auto; padding: 24px 14px 0; color: var(--muted); font-size: 13px; }
  .sidebar-foot strong { display: block; color: #c5d0d7; font-weight: 500; margin-bottom: 4px; }
  .workspace { min-width: 0; padding: 0 36px 28px; max-width: 1760px; width: 100%; margin: 0 auto; }
  header { min-height: 80px; display: flex; align-items: center; justify-content: space-between;
    gap: 16px; border-bottom: 1px solid var(--line); flex-wrap: wrap; padding: 16px 0; }
  .top-label { color: var(--muted); font-size: 14px; letter-spacing: .7px; }
  .top-label span { color: var(--ink); margin-left: 10px; }
  .top-controls { display: flex; gap: 20px; align-items: center; flex-wrap: wrap; }
  .pill { display: inline-flex; align-items: center; gap: 8px; padding: 5px 10px;
    border-radius: 6px; background: var(--surface); border: 1px solid var(--line); color: #c8d6dc; font-size: 13px; }
  .dot { width: 6px; height: 6px; border-radius: 50%; background: #8f9da6; }
  .userbox { display: flex; align-items: center; gap: 12px; font-size: 14px; }
  .hint { margin: 10px 0 18px; color: var(--muted); font-size: 14px; line-height: 1.9; }
  .gateway-hint { margin: 20px 0 0; padding: 12px 16px; border: 1px solid var(--line);
    border-left: 2px solid #4c665f; border-radius: 6px; background: #151a1d; font-size: 13px; }
  details.gateway-hint { cursor: pointer; }
  details.gateway-hint > summary { list-style: none; display: flex; align-items: center;
    justify-content: space-between; gap: 12px; }
  details.gateway-hint > summary::-webkit-details-marker { display: none; }
  details.gateway-hint > summary::after { content: "展开"; color: var(--blue); font-size: 12px; }
  details.gateway-hint[open] > summary::after { content: "收起"; }
  details.gateway-hint > .gateway-body { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--line); }
  code, pre { font-family: "Cascadia Code", Consolas, monospace; font-size: 14px; }
  code { background: #252e33; padding: 2px 6px; border-radius: 4px; color: #c7d9df; }
  pre { max-width: 100%; overflow: auto; background: #121719; border: 1px solid var(--line);
    padding: 18px; border-radius: 8px; line-height: 1.8; color: #b8d5cd; }
  .panel { display: none; min-width: 0; }
  .panel.active { display: block; }
  .page-heading { padding: 29px 0 24px; }
  .eyebrow { color: var(--blue); font-size: 12px; font-weight: 600; letter-spacing: 2px; margin-bottom: 6px; }
  .page-heading h1 { margin: 0; font-size: 28px; font-weight: 650; letter-spacing: -.6px; line-height: 1.5; }
  .page-heading p { color: var(--muted); margin: 6px 0 0; font-size: 14px; max-width: 78ch; }
  .stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin-bottom: 24px; }
  .stat { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 19px 22px; }
  .stat .k { font-size: 13px; color: var(--muted); }
  .stat .v { font-size: 31px; font-weight: 600; letter-spacing: -.7px; margin-top: 8px;
    font-variant-numeric: tabular-nums; line-height: 1.4; }
  .card { min-width: 0; background: var(--card); border: 1px solid var(--line); border-radius: 10px;
    padding: 22px; box-shadow: 0 4px 14px #00000012; }
  .card.wide { margin-top: 22px; }
  .mode-row { display: flex; gap: 14px; align-items: flex-end; flex-wrap: wrap; margin-bottom: 16px;
    padding-bottom: 16px; border-bottom: 1px solid var(--line); }
  .mode-row .field { flex: 0 0 300px; }
  .mode-row .mode-note { color: var(--muted); font-size: 13px; padding-bottom: 10px; }
  .sys-switch { display: flex; gap: 10px; margin: 0 0 18px; flex-wrap: wrap; }
  .sys-switch-btn { flex: 1 1 220px; display: flex; align-items: center; justify-content: center;
    gap: 8px; padding: 13px 18px; border-radius: 9px; border: 1px solid var(--line);
    background: var(--card); color: var(--muted); font-weight: 600; cursor: pointer;
    transition: border-color .18s ease, background .18s ease, color .18s ease; }
  .sys-switch-btn:hover { border-color: #3a4a52; color: var(--ink); }
  .sys-switch-btn.active { background: var(--accent-bg); border-color: #2c5149; color: #90e3d2;
    box-shadow: inset 0 0 0 1px #65d6c133; }
  .sys-subtabs { display: flex; gap: 8px; flex-wrap: wrap; margin: 0 0 18px; }
  .sys-subtab { padding: 8px 14px; border-radius: 7px; border: 1px solid var(--line);
    background: transparent; color: var(--muted); cursor: pointer; font-size: 14px; }
  .sys-subtab:hover { color: var(--ink); border-color: #3a4a52; }
  .sys-subtab.active { background: var(--surface); border-color: #3a4a52; color: var(--ink); }
  .sys-subpane { display: none; }
  .sys-subpane.active { display: block; }
  .params-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 12px; margin: 0 0 16px; }
  .param-item { background: var(--surface); border: 1px solid var(--line); border-radius: 8px;
    padding: 14px 16px; min-width: 0; }
  .param-item .k { color: var(--muted); font-size: 12.5px; letter-spacing: .3px; }
  .param-item .v { font-size: 21px; font-weight: 600; margin-top: 6px; line-height: 1.35;
    font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
  #crew-panel { margin: 14px 0 6px; }
  .crew-title { color: var(--muted); font-size: 13px; margin-bottom: 10px; }
  .crew-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }
  .crew-card { border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; background: var(--surface); }
  .crew-name { display: flex; justify-content: space-between; gap: 10px; font-weight: 600; }
  .crew-state { color: var(--muted); font-size: 12px; font-weight: 400; }
  .crew-meta { color: var(--muted); font-size: 13px; margin-top: 6px; }
  .crew-cites { margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap; }
  .crew-cite { font-size: 12px; color: #b5d5cc; border: 1px solid #2c5149; border-radius: 5px;
    padding: 2px 7px; background: #16211f; }
  details.card { padding: 0; overflow: hidden; }
  details.card > summary { list-style: none; cursor: pointer; display: flex; align-items: center;
    justify-content: space-between; gap: 14px; padding: 18px 22px; min-height: 58px; }
  details.card > summary::-webkit-details-marker { display: none; }
  details.card > summary:hover { background: #1e2529; }
  details.card > summary .summary-title { font-size: 17px; font-weight: 600; letter-spacing: .2px; }
  details.card > summary .summary-meta { color: var(--muted); font-size: 13px; font-weight: 400; margin-left: 10px; }
  details.card > summary::after { content: "展开"; color: var(--blue); font-size: 13px; flex: 0 0 auto; }
  details.card[open] > summary::after { content: "收起"; }
  details.card[open] > summary { border-bottom: 1px solid var(--line); margin-bottom: 18px; }
  details.card > .card-body { padding: 0 22px 22px; }
  .fold { border-top: 1px solid var(--line); }
  .fold:first-of-type { border-top: 0; }
  .fold > summary { list-style: none; cursor: pointer; display: flex; align-items: center;
    justify-content: space-between; gap: 12px; padding: 14px 0; color: #c9d5db; font-size: 15px; font-weight: 600; }
  .fold > summary::-webkit-details-marker { display: none; }
  .fold > summary::after { content: "展开"; color: var(--muted); font-size: 12px; font-weight: 400; }
  .fold[open] > summary::after { content: "收起"; }
  .fold > summary:hover { color: var(--ink); }
  .fold > .fold-body { padding: 0 0 12px; }
  summary:focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
  .fold .table-scroll { max-height: 420px; }
  h2 { font-size: 16px; font-weight: 600; margin: 0 0 18px; letter-spacing: .2px; }
  .card > h2 { padding-bottom: 14px; border-bottom: 1px solid var(--line); }
  h3 { font-size: 14px; margin: 20px 0 10px; color: var(--muted); }
  .table-scroll { width: 100%; overflow: auto; overscroll-behavior: contain;
    max-height: min(520px, 70vh); border-radius: 6px; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { text-align: left; padding: 13px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { position: sticky; top: 0; z-index: 2; color: #9cabb4; font-weight: 500; font-size: 13px;
    background: #151b1f; white-space: nowrap; }
  td { min-width: 80px; max-width: 360px; overflow-wrap: anywhere; }
  td code { white-space: normal; }
  td button { margin: 2px 0; white-space: nowrap; }
  tbody tr:last-child td { border-bottom: 0; }
  tbody tr:nth-child(even) { background: #1b2125; }
  tbody tr:hover { background: #20282c; }
  tr[data-trace] { cursor: pointer; }
  .badge { display: inline-block; padding: 2px 8px; border: 1px solid currentColor;
    border-radius: 5px; font-size: 12px; font-weight: 600; white-space: nowrap; }
  .pass { background: #1b342b; color: var(--green); } .warn { background: #382f20; color: var(--amber); }
  .fail { background: #3b2529; color: var(--red); } .review { background: #243341; color: #9cc6f0; }
  .muted { color: var(--muted); }
  .empty { padding: 40px 12px; text-align: center; color: var(--muted); font-size: 14px; }
  .alert { padding: 16px 0; border-bottom: 1px solid var(--line); }
  .alert:last-child { border-bottom: 0; }
  .alert .top { display: flex; align-items: center; gap: 8px; font-size: 13px; flex-wrap: wrap; }
  .alert .msg { margin-top: 7px; font-size: 14px; font-weight: 600; }
  .alert .meta { margin-top: 5px; font-size: 13px; color: var(--muted); }
  .sev { font-size: 13px; font-weight: 700; }
  .sev-high, .sev-critical { color: var(--red); } .sev-medium { color: var(--amber); }
  .sev-low, .sev-info { color: #9cc6f0; }
  /* 审计五视图 */
  .view-tabs { display: flex; gap: 8px; flex-wrap: wrap; margin: 18px 0 16px;
    border-bottom: 1px solid var(--line); padding-bottom: 12px; }
  .view-tab { padding: 6px 12px; font-size: 13px; border-radius: 999px;
    border: 1px solid #ffffff1a; background: transparent; color: var(--muted); }
  .view-tab.active { background: var(--accent-bg); border-color: #2c5149; color: #90e3d2; }
  .view-pane[hidden] { display: none; }
  .view-note { margin: 0 0 14px; color: var(--muted); font-size: 13px; line-height: 1.8; }
  .res-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
  .res-card { padding: 14px 16px; border: 1px solid var(--line); border-left-width: 3px;
    border-radius: 9px; background: #171d20; }
  .res-card.risk-read { border-left-color: #60d3aa; }
  .res-card.risk-write { border-left-color: #ff8585; }
  .res-card.risk-exec { border-left-color: #edc279; }
  .res-card.risk-blocked { border-left-color: #ff8585; background: #241a1c; }
  .res-head { display: flex; align-items: center; justify-content: space-between; gap: 8px;
    font-size: 12px; color: var(--muted); }
  .res-name { margin: 6px 0 4px; font-size: 14px; font-weight: 600; overflow-wrap: anywhere; }
  .res-meta { color: var(--muted); font-size: 12.5px; line-height: 1.7; }
  .res-actions { margin-top: 10px; display: flex; gap: 8px; flex-wrap: wrap; }
  .perm-row { display: grid; grid-template-columns: 84px minmax(0, 1fr) 96px; gap: 10px;
    padding: 11px 8px; border-bottom: 1px solid var(--line); font-size: 13.5px; }
  .perm-row:last-child { border-bottom: 0; }
  .perm-meta { color: var(--muted); font-size: 12.5px; }
  .chain { display: grid; gap: 8px; margin: 0 0 18px; }
  .chain-step { display: grid; grid-template-columns: 104px minmax(0, 1fr); gap: 10px;
    padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px;
    background: #171d20; font-size: 13.5px; }
  .chain-step .label { color: var(--blue); font-size: 12.5px; letter-spacing: .4px; }
  .chain-step .text { color: #cbd7db; line-height: 1.75; overflow-wrap: anywhere; }
  .chain-title { display: flex; justify-content: space-between; gap: 10px; margin-bottom: 10px;
    font-weight: 600; font-size: 14px; }
  .impact-banner { margin-bottom: 14px; padding: 10px 14px; border: 1px solid #675637;
    background: #29271e; border-radius: 7px; color: var(--amber); font-size: 13px; line-height: 1.8; }
  /* HOTL 监管指标 */
  .hotl-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; }
  .hotl-item { padding: 14px 16px; border: 1px solid var(--line); border-radius: 9px;
    background: #171d20; color: inherit; text-align: left; transition: border-color .18s, transform .18s; }
  .hotl-item:hover:not(:disabled) { border-color: #65d6c166; background: #1b2226; transform: translateY(-2px); }
  .hotl-item .k { color: var(--muted); font-size: 12.5px; }
  .hotl-item .v { margin-top: 6px; font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .hotl-item .s { margin-top: 2px; color: var(--muted); font-size: 12px; }
  .first-run { margin: 0 0 18px; padding: 12px 16px; border: 1px solid #2c5149; border-left: 3px solid var(--blue);
    border-radius: 8px; background: #16211f; color: #b9d6cf; font-size: 13.5px; line-height: 1.85; }
  /* 审批 */
  .approval-risk { display: inline-block; margin-left: 6px; padding: 0 7px; border-radius: 999px;
    font-size: 11.5px; border: 1px solid currentColor; white-space: nowrap; }
  .approval-risk.high { color: #ff9f9f; }
  .approval-risk.medium { color: var(--amber); }
  .approval-risk.low { color: var(--muted); }
  .approval-note { margin-top: 6px; width: 100%; min-width: 130px; background: #12181b; color: var(--ink);
    border: 1px solid var(--line); border-radius: 5px; padding: 5px 8px; font-size: 12.5px; }
  .approval-rate { color: var(--muted); font-size: 12.5px; }
  footer { margin-top: 32px; padding-top: 18px; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; }
  .replay { display: none; margin-top: 22px; }
  .replay-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .replay-head h2 { margin: 0; }
  .replay-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  button { border: 1px solid #3a464d; background: #242d32; color: #dce5e9;
    padding: 8px 14px; border-radius: 6px; font-size: 14px; font-weight: 500; cursor: pointer;
    transition: background .15s, border-color .15s; }
  button:hover:not(:disabled) { border-color: #71868d; background: #2d393e; }
  button.tab:hover { background: #202b2d; }
  button.tab.active:hover { background: var(--accent-bg); border-color: #497b6e; }
  button.primary { background: #68d4bf; border-color: #68d4bf; color: #102923; font-weight: 650; }
  button.primary:hover:not(:disabled) { background: #8ee2d1; border-color: #8ee2d1; }
  button.danger { background: #38252a; border-color: #75454d; color: #ffabab; }
  button.small { padding: 4px 9px; font-size: 13px; }
  button:disabled { opacity: .4; cursor: not-allowed; }
  .replay-actions a { font-size: 14px; text-decoration: none; }
  .track { position: relative; height: 48px; margin: 22px 0 14px; border: 1px solid var(--line);
    border-radius: 7px; background: #11181b; overflow: hidden; }
  .bar { position: absolute; top: 12px; height: 22px; border-radius: 4px; opacity: .45; transition: opacity .2s ease; }
  .bar.active { opacity: 1; }
  .bar.kind-llm { background: #9c9ccf; } .bar.kind-tool { background: #d2ad69; }
  .bar.kind-agent { background: #65bca6; } .bar.kind-retrieval { background: #79a9c6; }
  .bar.kind-handoff { background: #ad8fbf; } .bar.kind-human { background: #8f9eaf; }
  .bar.kind-system { background: #94a3b8; }
  .playhead { position: absolute; top: 0; bottom: 0; width: 2px; background: var(--blue);
    left: 0; transition: left .2s linear; }
  .rrow { display: grid; grid-template-columns: 84px minmax(0, 1fr); gap: 12px; padding: 14px 10px;
    border-bottom: 1px solid var(--line); font-size: 14px; border-radius: 5px; }
  .rrow:last-child { border-bottom: 0; } .rrow.active { background: var(--accent-bg); }
  .rrow .kind { font-size: 12px; font-weight: 600; text-transform: uppercase; color: var(--muted); }
  .rrow .name { font-weight: 600; } .rrow .sum { color: var(--muted); font-size: 14px; margin-top: 4px; }
  .finding { margin-top: 6px; font-size: 14px; color: var(--red); } .finding b { font-weight: 600; }
  .form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px 24px; }
  .field { min-width: 0; display: flex; flex-direction: column; gap: 8px; font-size: 14px; color: #b0bec6; }
  .field input, .field select, .field textarea, .login-card input { width: 100%; min-width: 0;
    border: 1px solid #38444b; border-radius: 6px; padding: 10px 12px; font-size: 15px;
    color: var(--ink); background: #12191c; font-family: inherit; }
  input::placeholder, textarea::placeholder { color: #7f8d96; }
  input:focus, select:focus, textarea:focus { border-color: var(--blue); }
  input:read-only { background: #252c30; color: #b2bec5; border-style: dashed; }
  input:disabled, select:disabled, textarea:disabled { opacity: .5; cursor: not-allowed; }
  input[type="checkbox"] { width: 15px; height: 15px; flex: 0 0 15px; accent-color: var(--blue); margin: 2px 0; }
  .field.full { grid-column: 1 / -1; }
  #agent-model-field[hidden] { display: none; }
  .checks { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px 20px;
    font-size: 14px; margin: 10px 0; }
  .checks label { display: flex; gap: 9px; align-items: center; color: #c4cfd5; }
  .actions { display: flex; align-items: center; gap: 10px; margin: 22px 0 8px; flex-wrap: wrap; }
  .actions .muted { font-size: 14px; }
  /* 代码检查：拖拽上传区 */
  .dropzone { margin-top: 16px; padding: 26px 18px; border: 1px dashed #3a5a53; border-radius: 10px;
    background: linear-gradient(180deg, #1a2321, #161c1e); text-align: center; cursor: pointer;
    transition: border-color .18s ease, background .18s ease, box-shadow .18s ease; }
  .dropzone:hover { border-color: #4f8a7d; background: #1a2522; }
  .dropzone.dragover { border-color: var(--blue); background: #193630;
    box-shadow: inset 0 0 0 1px #65d6c140, 0 0 24px #65d6c118; }
  .dropzone[aria-busy="true"] { opacity: .65; pointer-events: none; }
  .dz-icon { font-size: 22px; line-height: 1; color: var(--blue); }
  .dz-title { margin-top: 10px; font-weight: 600; color: #d3e8e2; }
  .dz-hint { margin-top: 5px; color: var(--muted); font-size: 13px; }
  .dz-alt { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 12px; font-size: 13px; }
  fieldset { min-width: 0; border: 1px solid #43544f; border-radius: 8px; margin: 22px 0 0; padding: 20px; }
  legend { color: #b5d5cc; padding: 0 8px; font-size: 14px; }
  .notice { margin: 12px 0; padding: 12px 16px; border-radius: 6px; background: #362f20;
    border: 1px solid #675637; color: var(--amber); font-size: 14px; display: none; }
  .notice.show { display: block; }
  .notice.error { background: #382328; border-color: #77464c; color: #ffabab; }
  .runtime { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; margin-bottom: 22px; }
  .chip { display: inline-block; max-width: 100%; margin: 4px 5px 4px 0; padding: 4px 10px;
    border: 1px solid #75454d; border-radius: 5px; background: #38252a; color: #ffabab; font-size: 13px; }
  .muted-chip { background: #243341; border-color: #3f566a; color: #acc8e4; }
  #model-mode, #model-judge { padding: 10px 14px; background: #141b1e; border-radius: 6px; margin: 8px 0; }
  #agent-detail > .field { margin-top: 18px; }
  #agent-secret-box { margin: 20px 0; padding: 18px; border: 1px solid #746039; border-radius: 8px; background: #29271e; }
  /* ---------- 初始介绍页 ---------- */
  #landing { position: fixed; inset: 0; z-index: 40; overflow-y: auto; overflow-x: hidden;
    background:
      radial-gradient(900px 520px at 80% -10%, #16443c 0%, transparent 62%),
      radial-gradient(760px 520px at 4% 104%, #1b2b44 0%, transparent 60%),
      #0d1114; }
  #landing::before { content: ""; position: fixed; inset: 0; pointer-events: none;
    background-image: linear-gradient(#ffffff09 1px, transparent 1px),
      linear-gradient(90deg, #ffffff09 1px, transparent 1px);
    background-size: 46px 46px;
    -webkit-mask-image: radial-gradient(ellipse 82% 62% at 50% 0%, #000 18%, transparent 78%);
    mask-image: radial-gradient(ellipse 82% 62% at 50% 0%, #000 18%, transparent 78%); }
  #landing-canvas { position: fixed; inset: 0; z-index: 1; width: 100%; height: 100%;
    pointer-events: none; opacity: .3; }
  #landing::after { content: ""; position: fixed; inset: 0; z-index: 1; pointer-events: none;
    background: radial-gradient(430px circle at var(--mx, 50%) var(--my, 24%), #65d6c10d, transparent 64%); }
  .landing-shell { position: relative; z-index: 2; max-width: 1180px; margin: 0 auto; padding: 30px 32px 46px; }
  .landing-nav { display: flex; align-items: center; justify-content: space-between; gap: 18px;
    flex-wrap: wrap; padding-bottom: 20px; border-bottom: 1px solid #ffffff12; }
  .landing-logo { font-size: 22px; font-weight: 750; letter-spacing: -.6px; line-height: 1.4; }
  .landing-logo span { color: var(--blue); }
  .landing-logo small { margin-left: 10px; color: var(--muted); font-size: 12px; letter-spacing: 3px; font-weight: 500; }
  .landing-nav-right { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
  .landing-status { display: inline-flex; align-items: center; gap: 8px; padding: 6px 12px; border-radius: 999px;
    border: 1px solid #ffffff1a; background: #ffffff08; color: #c2d3d8; font-size: 13px; }
  .landing-status .dot { background: #60d3aa; box-shadow: 0 0 0 4px #60d3aa22; }
  .landing-status.offline .dot { background: var(--red); box-shadow: 0 0 0 4px #ff858522; }
  .landing-hero { display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(280px, .85fr);
    gap: 40px; align-items: center; padding: 58px 0 48px; }
  .landing-copy h1 { margin: 10px 0 18px; font-size: clamp(29px, 4.1vw, 45px); line-height: 1.3; letter-spacing: -.9px; }
  .landing-copy h1 em { font-style: normal; background: linear-gradient(100deg, #65d6c1 0%, #b8f4e2 26%,
    #b4a5e8 52%, #65d6c1 78%, #65d6c1 100%); background-size: 260% 100%;
    -webkit-background-clip: text; background-clip: text; color: transparent;
    animation: landingFlow 11s linear infinite; }
  .landing-copy { position: relative; }
  .landing-copy::before { content: ""; position: absolute; inset: -26px -34px; z-index: -1;
    pointer-events: none; border-radius: 30px;
    background: radial-gradient(112% 108% at 10% 32%, #0d1114e6 0%, #0d1114a6 46%, #0d111400 76%); }
  .landing-copy p { color: #a9b7bf; font-size: 15.5px; line-height: 1.95; max-width: 58ch; margin: 0; }
  .landing-cta { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 26px; align-items: center; }
  .landing-cta button { padding: 12px 22px; font-size: 15px; }
  .landing-cta .ghost { border-color: #ffffff26; color: #cbd9dd; background: transparent; }
  .landing-cta .ghost:hover { border-color: #65d6c166; color: var(--ink); }
  .landing-facts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px;
    margin: 32px 0 0; padding: 0; list-style: none; }
  .landing-facts li { padding: 14px 16px; border: 1px solid #ffffff14; border-radius: 10px; background: #ffffff06; }
  .landing-facts b { display: block; font-size: 19px; color: #e9f3f0; letter-spacing: -.3px; }
  .landing-facts span { color: var(--muted); font-size: 12.5px; }
  .landing-visual { position: relative; display: grid; place-items: center; min-height: 330px; }
  .landing-glow { position: absolute; width: 250px; height: 250px; border-radius: 50%;
    background: radial-gradient(circle, #65d6c152 0%, transparent 68%); filter: blur(28px);
    animation: landingBreathe 7s ease-in-out infinite; }
  .orbit { position: relative; width: 292px; height: 292px; display: grid; place-items: center; }
  .orbit::before { content: ""; position: absolute; width: 268px; height: 268px; border-radius: 50%;
    background: conic-gradient(from 0deg, #65d6c100 0deg, #65d6c100 248deg, #65d6c13d 328deg, #65d6c100 360deg);
    animation: landingSpin 9s linear infinite; }
  .orbit-ring { position: absolute; border-radius: 50%; border: 1px solid #65d6c12e; }
  .orbit-ring::after { content: ""; position: absolute; top: -4px; left: calc(50% - 3px);
    width: 6px; height: 6px; border-radius: 50%; background: var(--blue); box-shadow: 0 0 12px var(--blue); }
  .orbit-ring.r1 { width: 128px; height: 128px; border-style: dashed; animation: landingSpin 17s linear infinite; }
  .orbit-ring.r2 { width: 196px; height: 196px; border-color: #b4a5e833; animation: landingSpin 26s linear infinite reverse; }
  .orbit-ring.r2::after { background: var(--purple); box-shadow: 0 0 12px var(--purple); }
  .orbit-ring.r3 { width: 268px; height: 268px; border-color: #edc27926; animation: landingSpin 36s linear infinite; }
  .orbit-ring.r3::after { background: var(--amber); box-shadow: 0 0 12px var(--amber); }
  .orbit-core { width: 74px; height: 74px; border-radius: 50%; display: grid; place-items: center;
    background: radial-gradient(circle at 34% 30%, #1f4a41, #0f1a19 72%);
    border: 1px solid #65d6c166; color: var(--blue); font-size: 29px; font-weight: 750;
    box-shadow: 0 0 44px #65d6c12e, inset 0 0 20px #65d6c114;
    animation: landingCore 4.6s ease-in-out infinite; }
  .landing-section { padding: 44px 0 6px; border-top: 1px solid #ffffff10; }
  .landing-section-head { position: relative; margin-bottom: 24px; }
  .landing-section-head::before { content: ""; position: absolute; inset: -18px -26px; z-index: -1;
    pointer-events: none; border-radius: 22px;
    background: radial-gradient(100% 120% at 6% 42%, #0d1114d9 0%, #0d111477 58%, #0d111400 82%); }
  .landing-section-head h2 { margin: 6px 0 0; font-size: 23px; letter-spacing: -.4px; }
  .landing-section-head p { margin: 8px 0 0; color: var(--muted); font-size: 14px; max-width: 76ch; }
  .landing-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
  .review-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
  .landing-card { position: relative; padding: 22px; border: 1px solid #ffffff14; border-radius: 12px;
    background: linear-gradient(158deg, #ffffff0a, #ffffff03);
    transition: transform .25s ease, border-color .25s ease, box-shadow .25s ease; }
  .landing-card:hover { transform: translateY(-3px); border-color: #65d6c155; box-shadow: 0 16px 40px #00000040; }
  .landing-card::after { content: ""; position: absolute; inset: -1px; border-radius: 13px;
    padding: 1px; opacity: 0; pointer-events: none; transition: opacity .3s ease;
    background: conic-gradient(from var(--landing-angle, 0deg), #65d6c100 0deg, #65d6c1b0 46deg,
      #b4a5e8b0 92deg, #65d6c100 150deg);
    -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
    -webkit-mask-composite: xor;
    mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
    mask-composite: exclude; }
  .landing-card:hover::after { opacity: 1; animation: landingBorder 3.4s linear infinite; }
  .landing-card-num { font: 12px "Cascadia Code", Consolas, monospace; color: #65d6c1aa; letter-spacing: 2px; }
  .landing-card h3 { margin: 10px 0 8px; font-size: 16.5px; }
  .landing-card p { margin: 0; color: #9fadb5; font-size: 13.5px; line-height: 1.85; }
  .landing-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 14px; }
  .landing-chips span { font-size: 12px; padding: 3px 9px; border-radius: 999px;
    border: 1px solid #ffffff1a; color: #b9c9d0; background: #ffffff08; }
  .landing-flow { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px;
    margin: 0; padding: 0; list-style: none; counter-reset: landing-step; }
  .landing-flow li { position: relative; padding: 20px 18px; border-radius: 10px;
    background: #ffffff07; border: 1px solid #ffffff12; }
  .landing-flow li::before { counter-increment: landing-step; content: "0" counter(landing-step);
    font: 12px "Cascadia Code", Consolas, monospace; color: #65d6c1aa; letter-spacing: 2px; }
  .landing-flow strong { display: block; margin: 7px 0 5px; font-size: 15px; }
  .landing-flow span { color: var(--muted); font-size: 13px; line-height: 1.8; }
  .landing-more { margin-top: 28px; border: 1px solid #ffffff14; border-radius: 12px; background: #ffffff05; }
  .landing-more > summary { list-style: none; cursor: pointer; padding: 16px 20px; font-size: 14px; color: #cbd9dd; }
  .landing-more > summary::-webkit-details-marker { display: none; }
  .landing-more > summary::after { content: "展开"; float: right; color: var(--blue); font-size: 12px; }
  .landing-more[open] > summary::after { content: "收起"; }
  .landing-more-body { padding: 0 20px 18px; color: var(--muted); font-size: 13.5px; line-height: 1.9; }
  .landing-more-body p { margin: 0 0 8px; }
  .landing-foot { margin-top: 42px; padding-top: 20px; border-top: 1px solid #ffffff10;
    display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap;
    color: var(--muted); font-size: 12.5px; }
  .landing-rise { animation: landingRise .75s ease both; }
  .landing-rise.d1 { animation-delay: .1s; } .landing-rise.d2 { animation-delay: .2s; }
  .landing-rise.d3 { animation-delay: .3s; }
  .landing-reveal { opacity: 0; transform: translateY(18px);
    transition: opacity .7s ease, transform .7s ease; }
  .landing-reveal.in { opacity: 1; transform: none; }
  @property --landing-angle { syntax: "<angle>"; inherits: false; initial-value: 0deg; }
  @keyframes landingRise { from { opacity: 0; transform: translateY(14px); } to { opacity: 1; transform: none; } }
  @keyframes landingSpin { to { transform: rotate(360deg); } }
  @keyframes landingFlow { to { background-position: 260% 0; } }
  @keyframes landingBorder { to { --landing-angle: 360deg; } }
  @keyframes landingCore {
    0%, 100% { box-shadow: 0 0 30px #65d6c122, inset 0 0 18px #65d6c110; }
    50% { box-shadow: 0 0 62px #65d6c155, inset 0 0 26px #65d6c11f; }
  }
  @keyframes landingBreathe { 0%, 100% { opacity: .55; transform: scale(.94); } 50% { opacity: .95; transform: scale(1.06); } }
  #login { position: fixed; inset: 0; background: #0d1115ed; backdrop-filter: blur(8px); display: none;
    align-items: center; justify-content: center; z-index: 50; padding: 20px; overflow-y: auto; }
  #login.show { display: flex; }
  .login-card { width: 420px; max-width: 100%; max-height: 100%; overflow-y: auto; background: #191f23;
    border: 1px solid #364249; border-radius: 14px; padding: 34px; box-shadow: 0 24px 90px #0006; }
  .login-mark { width: 38px; height: 38px; display: grid; place-items: center; border: 1px solid #41685e;
    border-radius: 10px; background: var(--accent-bg); color: var(--blue); font-size: 21px; margin-bottom: 20px; }
  .login-card h1 { font-size: 27px; margin: 0 0 6px; letter-spacing: -.6px; }
  .login-subtitle { color: var(--muted); margin-bottom: 24px; font-size: 14px; }
  .login-card .muted { margin: 22px 0 0; padding-top: 18px; border-top: 1px solid var(--line); font-size: 13px; line-height: 1.9; }
  .login-card label { display: block; font-size: 14px; color: #b0bec6; margin: 16px 0 7px; }
  .login-card button { width: 100%; margin-top: 24px; padding: 11px; }
  .login-card .login-back { width: auto; margin: 0 0 16px; padding: 4px 0; background: none;
    border: 0; color: var(--blue); font-size: 13px; text-align: left; }
  .login-card .login-back:hover { text-decoration: underline; text-underline-offset: 4px; }
  .login-msg { margin-top: 10px; font-size: 14px; color: var(--red); min-height: 18px; }
  /* ============================================================
     Linear 风格视觉层（仅改视觉：颜色 / 字体 / 密度 / 圆角 / 阴影）
     结构与交互保持不变：所有 id、class、事件、接口调用均未改动。
     ============================================================ */
  :root {
    --bg: #08090a; --bg-elev: #0b0c0e; --card: #0e0f11; --surface: #17181b;
    --ink: #f7f8f8; --muted: #8b9098; --line: #1f2124; --line-strong: #2b2e33;
    --green: #4ec9a3; --red: #f0736f; --amber: #d6a45f;
    --blue: #4ed3b8; --purple: #a79be0; --accent-bg: rgba(78, 211, 184, .10);
  }
  body { background: var(--bg); color: var(--ink); font-size: 13.5px; line-height: 1.55;
    font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }
  ::selection { background: rgba(78, 211, 184, .24); }
  a { color: #7ddcc7; }
  :focus-visible { outline: 2px solid rgba(78, 211, 184, .55); outline-offset: 2px; }
  button, input, select, textarea { letter-spacing: .005em; }
  /* 侧边栏 */
  .sidebar { background: #0a0b0d; padding: 18px 10px 14px; }
  .brand { font-size: 15px; font-weight: 600; letter-spacing: -.01em; padding: 0 8px; }
  .brand small { margin-top: 3px; font-size: 10.5px; letter-spacing: .14em; font-weight: 500; }
  .nav-caption { margin: 26px 8px 8px; font-size: 10.5px; letter-spacing: .12em; color: #6d727a; }
  .tabs { gap: 1px; }
  .tab { padding: 6px 8px; gap: 8px; border-radius: 6px; font-size: 13px; font-weight: 500; color: #9aa0a8; }
  .tab::before { font-size: 10.5px; opacity: .5; }
  .tab:hover { background: rgba(255, 255, 255, .04); color: #d6d9dd; }
  .tab.active { background: rgba(255, 255, 255, .07); border-color: transparent; color: #f7f8f8; box-shadow: none; }
  .sidebar-foot { padding: 18px 8px 0; font-size: 11.5px; }
  .sidebar-foot strong { font-size: 11.5px; color: #b9bec5; }
  /* 顶栏 */
  header { min-height: 52px; padding: 10px 0; }
  .top-label { font-size: 12.5px; letter-spacing: 0; }
  .top-label span { color: #d7dade; margin-left: 8px; }
  .top-controls { gap: 10px; }
  .pill { padding: 2px 8px; border-radius: 5px; background: rgba(255, 255, 255, .03);
    border-color: var(--line); color: #b6bcc4; font-size: 11.5px; }
  .dot { width: 5px; height: 5px; }
  .userbox { gap: 10px; font-size: 12.5px; }
  /* 页面标题 */
  .page-heading { padding: 24px 0 16px; }
  .eyebrow { font-size: 10.5px; font-weight: 500; letter-spacing: .1em; color: #7f8a8f; margin-bottom: 5px; }
  .page-heading h1 { font-size: 22px; font-weight: 600; letter-spacing: -.02em; line-height: 1.35; }
  .page-heading p { font-size: 13px; margin-top: 5px; }
  /* 指标与卡片 */
  .stats { gap: 10px; margin-bottom: 16px; }
  .stat { border-radius: 8px; padding: 13px 15px; }
  .stat .k { font-size: 11.5px; }
  .stat .v { margin-top: 4px; font-size: 25px; font-weight: 600; letter-spacing: -.02em; }
  .card { border-radius: 8px; padding: 18px; box-shadow: none; }
  .card.wide { margin-top: 14px; }
  details.card > summary { padding: 11px 16px; min-height: 44px; }
  details.card > summary:hover { background: rgba(255, 255, 255, .025); }
  details.card > summary .summary-title { font-size: 13.5px; font-weight: 600; }
  details.card > summary .summary-meta { margin-left: 8px; font-size: 11.5px; }
  details.card > summary::after { font-size: 11px; color: var(--muted); }
  details.card[open] > summary { margin-bottom: 14px; }
  details.card > .card-body { padding: 0 16px 16px; }
  .fold > summary { padding: 10px 0; font-size: 13px; }
  .fold > summary::after { font-size: 11px; }
  h2 { margin-bottom: 12px; font-size: 14px; font-weight: 600; letter-spacing: 0; }
  .card > h2 { padding-bottom: 10px; }
  h3 { margin: 16px 0 8px; font-size: 12.5px; }
  .hint { margin: 8px 0 14px; font-size: 12.5px; line-height: 1.7; }
  .gateway-hint { margin-top: 14px; padding: 10px 12px; border-left-color: #2f4d47; border-radius: 6px; background: #0b0c0e; }
  .gateway-hint > summary::after { font-size: 11px; }
  /* 表格 */
  table { font-size: 12.5px; }
  th, td { padding: 8px 10px; }
  th { font-size: 11px; font-weight: 500; letter-spacing: .02em; color: #7f858d; background: #0b0c0e; }
  td { max-width: 380px; }
  tbody tr:nth-child(even) { background: transparent; }
  tbody tr:hover { background: rgba(255, 255, 255, .03); }
  .empty { padding: 32px 12px; font-size: 12.5px; }
  /* 状态徽章 */
  .badge { padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 500; }
  .pass { background: rgba(78, 201, 163, .10); color: #6fd7b6; border-color: rgba(78, 201, 163, .28); }
  .warn { background: rgba(214, 164, 95, .10); color: #dfb57a; border-color: rgba(214, 164, 95, .28); }
  .fail { background: rgba(240, 115, 111, .10); color: #f1938f; border-color: rgba(240, 115, 111, .28); }
  .review { background: rgba(120, 160, 220, .10); color: #9dc0e6; border-color: rgba(120, 160, 220, .24); }
  .alert { padding: 12px 0; }
  .alert .msg { font-size: 13px; }
  .alert .meta, .alert .top { font-size: 11.5px; }
  /* 按钮与表单 */
  button { padding: 5px 11px; border-radius: 6px; font-size: 12.5px; font-weight: 500;
    background: #1a1b1f; border-color: #2a2d32; color: #e4e6e8; }
  button:hover:not(:disabled) { background: #212328; border-color: #3a3e45; }
  button.tab:hover { background: rgba(255, 255, 255, .04); }
  button.tab.active:hover { background: rgba(255, 255, 255, .07); border-color: transparent; }
  button.primary { background: #4ed3b8; border-color: #4ed3b8; color: #04201a; font-weight: 600; }
  button.primary:hover:not(:disabled) { background: #6fe0c9; border-color: #6fe0c9; }
  button.danger { background: rgba(240, 115, 111, .12); border-color: rgba(240, 115, 111, .35); color: #f0938f; }
  button.small { padding: 3px 8px; font-size: 11.5px; }
  .replay-actions a { font-size: 12.5px; }
  .field { gap: 6px; font-size: 12.5px; color: #a8aeb5; }
  .field input, .field select, .field textarea, .login-card input { padding: 7px 10px; font-size: 13px;
    border-radius: 6px; background: #0b0c0e; border-color: #26282c; }
  input::placeholder, textarea::placeholder { color: #6d727a; }
  input:focus, select:focus, textarea:focus { border-color: rgba(78, 211, 184, .55);
    box-shadow: 0 0 0 3px rgba(78, 211, 184, .12); outline: none; }
  input:read-only { background: #15161a; }
  input[type="checkbox"] { width: 14px; height: 14px; flex-basis: 14px; }
  .form { gap: 14px 18px; }
  .actions { margin: 16px 0 6px; gap: 8px; }
  .actions .muted { font-size: 12.5px; }
  .checks { gap: 10px 16px; font-size: 12.5px; }
  .checks label { gap: 7px; color: #b9bfc6; }
  .notice { padding: 10px 12px; border-radius: 6px; font-size: 12.5px;
    background: rgba(214, 164, 95, .08); border-color: rgba(214, 164, 95, .25); }
  .notice.error { background: rgba(240, 115, 111, .08); border-color: rgba(240, 115, 111, .28); }
  code { padding: 1px 5px; border-radius: 4px; font-size: 12px; background: #17181b;
    border: 1px solid #222428; color: #cfd4d8; }
  pre { padding: 12px; border-radius: 6px; font-size: 12px; line-height: 1.7;
    background: #0b0c0e; border-color: #222428; color: #b9c6c2; }
  fieldset { border-color: #2a2d32; border-radius: 7px; padding: 14px; }
  legend { font-size: 12.5px; color: #b5d5cc; }
  #model-mode, #model-judge { padding: 8px 12px; background: #0b0c0e; border: 1px solid var(--line); border-radius: 6px; }
  #agent-secret-box { border-radius: 7px; border-color: rgba(214, 164, 95, .28); background: rgba(214, 164, 95, .07); }
  .chip { padding: 2px 8px; border-radius: 4px; font-size: 11.5px;
    background: rgba(240, 115, 111, .10); border-color: rgba(240, 115, 111, .28); }
  .muted-chip { background: rgba(120, 160, 220, .08); border-color: rgba(120, 160, 220, .22); color: #a9c4e2; }
  /* 五视图 */
  .view-tabs { gap: 2px; margin: 14px 0; padding-bottom: 8px; }
  .view-tab { padding: 4px 9px; border-radius: 5px; border-color: transparent; background: transparent;
    color: #9aa0a8; font-size: 12px; }
  .view-tab:hover { background: rgba(255, 255, 255, .04); color: #d6d9dd; }
  .view-tab.active { background: rgba(255, 255, 255, .07); border-color: transparent; color: #f7f8f8; }
  .view-note { font-size: 12px; line-height: 1.7; }
  .res-card, .chain-step, .hotl-item { border-radius: 7px; background: #0d0e10; }
  .res-card.risk-blocked { background: rgba(240, 115, 111, .06); }
  .res-name { font-size: 13px; }
  .res-meta, .perm-meta { font-size: 11.5px; }
  .hotl-item { padding: 12px 14px; }
  .hotl-item:hover:not(:disabled) { background: #121316; border-color: #33363b; transform: none; }
  .hotl-item .v { font-size: 19px; }
  .hotl-item .k, .hotl-item .s { font-size: 11.5px; }
  .chain-step { font-size: 12.5px; }
  .chain-step .label { font-size: 11.5px; }
  .perm-row { font-size: 12.5px; padding: 9px 6px; }
  .first-run { padding: 10px 12px; border-radius: 7px; border-color: rgba(78, 211, 184, .24);
    border-left-color: var(--blue); background: rgba(78, 211, 184, .06); color: #b9d6cf; font-size: 12.5px; }
  .impact-banner { padding: 9px 12px; border-radius: 6px; font-size: 12px;
    background: rgba(214, 164, 95, .08); border-color: rgba(214, 164, 95, .25); }
  .approval-note { background: #0b0c0e; border-color: #26282c; border-radius: 5px; font-size: 12px; }
  .replay { margin-top: 14px; }
  .track { height: 40px; margin: 16px 0 10px; background: #0b0c0e; border-radius: 6px; }
  .bar { top: 9px; height: 20px; }
  .rrow { padding: 11px 8px; font-size: 12.5px; }
  .rrow .kind { font-size: 10.5px; }
  .rrow .sum { font-size: 12.5px; }
  .finding { font-size: 12.5px; }
  /* 拖拽上传 */
  .dropzone { padding: 22px 16px; border-radius: 8px; border-color: #2b2e33; background: transparent; }
  .dropzone:hover { background: rgba(255, 255, 255, .02); border-color: #3a3e45; }
  .dropzone.dragover { background: rgba(78, 211, 184, .07); border-color: rgba(78, 211, 184, .55); box-shadow: none; }
  .dz-icon { font-size: 18px; }
  .dz-title { margin-top: 8px; font-size: 13px; }
  .dz-hint { font-size: 11.5px; }
  .dz-alt { font-size: 11.5px; gap: 10px; }
  footer { margin-top: 24px; padding-top: 14px; font-size: 11px; }
  /* 初始介绍页 */
  #landing { background:
      radial-gradient(900px 520px at 78% -12%, rgba(78, 211, 184, .10) 0%, transparent 62%),
      radial-gradient(700px 480px at 2% 104%, rgba(96, 122, 190, .08) 0%, transparent 60%),
      #08090a; }
  #landing::before { background-image: linear-gradient(#ffffff07 1px, transparent 1px),
      linear-gradient(90deg, #ffffff07 1px, transparent 1px); background-size: 44px 44px; }
  #landing-canvas { opacity: .18; }
  #landing::after { background: radial-gradient(420px circle at var(--mx, 50%) var(--my, 24%), rgba(78, 211, 184, .06), transparent 64%); }
  .landing-shell { max-width: 1120px; padding: 24px 28px 40px; }
  .landing-nav { padding-bottom: 14px; }
  .landing-logo { font-size: 16px; font-weight: 650; letter-spacing: -.01em; }
  .landing-logo small { margin-left: 8px; font-size: 10.5px; letter-spacing: .12em; }
  .landing-nav-right { gap: 10px; }
  .landing-status { padding: 3px 9px; border-radius: 5px; background: rgba(255, 255, 255, .03);
    border-color: var(--line); color: #b8c2c6; font-size: 11.5px; }
  .landing-hero { padding: 52px 0 44px; gap: 36px; }
  .landing-copy h1 { margin: 8px 0 16px; font-size: clamp(30px, 4vw, 44px); line-height: 1.18;
    letter-spacing: -.03em; font-weight: 600; }
  .landing-copy h1 em { background: linear-gradient(100deg, #8ff0dc 0%, #ffffff 46%, #7ddcc7 100%);
    background-size: 100% 100%; -webkit-background-clip: text; background-clip: text;
    color: transparent; animation: none; }
  .landing-copy p { font-size: 14px; line-height: 1.8; color: #9ba1a9; }
  .landing-cta { margin-top: 22px; gap: 8px; }
  .landing-cta button { padding: 7px 14px; font-size: 13px; }
  .landing-cta .ghost { border-color: #2a2d32; color: #c3c8ce; }
  .landing-facts { gap: 10px; margin-top: 26px; }
  .landing-facts li { padding: 11px 13px; border-radius: 8px; border-color: var(--line); background: transparent; }
  .landing-facts b { font-size: 15px; font-weight: 600; color: #e9f3f0; }
  .landing-facts span { font-size: 11.5px; }
  .landing-glow { opacity: .5; filter: blur(34px); }
  .orbit::before { background: conic-gradient(from 0deg, transparent 0deg, transparent 250deg,
    rgba(78, 211, 184, .20) 330deg, transparent 360deg); }
  .orbit-ring { border-color: rgba(78, 211, 184, .14); }
  .orbit-ring.r2, .orbit-ring.r3 { border-color: rgba(255, 255, 255, .08); }
  .orbit-core { background: radial-gradient(circle at 34% 30%, #12332d, #0a1110 72%); }
  .landing-section { padding: 38px 0 4px; border-top-color: var(--line); }
  .landing-section-head { margin-bottom: 18px; }
  .landing-section-head h2 { font-size: 19px; font-weight: 600; letter-spacing: -.01em; }
  .landing-section-head p { font-size: 13px; }
  .landing-grid, .review-grid { gap: 10px; }
  .landing-card { padding: 16px; border-radius: 8px; background: #0d0e10; border-color: var(--line); }
  .landing-card:hover { transform: none; border-color: #34373c; box-shadow: none; }
  .landing-card::after { display: none; }
  .landing-card-num { font-size: 10.5px; letter-spacing: .1em; color: #7f8a8f; }
  .landing-card h3 { margin: 7px 0 6px; font-size: 14px; font-weight: 600; }
  .landing-card p { font-size: 12.5px; line-height: 1.75; color: #9aa0a8; }
  .landing-chips { margin-top: 12px; gap: 5px; }
  .landing-chips span { padding: 1px 7px; border-radius: 4px; font-size: 11px;
    background: rgba(255, 255, 255, .03); border-color: var(--line); color: #a9afb6; }
  .landing-flow { gap: 10px; }
  .landing-flow li { padding: 15px; border-radius: 8px; background: #0d0e10; border-color: var(--line); }
  .landing-flow li::before { font-size: 10.5px; color: #7f8a8f; }
  .landing-flow strong { margin: 6px 0 4px; font-size: 13.5px; }
  .landing-flow span { font-size: 12.5px; }
  .landing-more { border-radius: 8px; border-color: var(--line); background: transparent; }
  .landing-more > summary { padding: 12px 16px; font-size: 12.5px; }
  .landing-more-body { font-size: 12.5px; }
  .landing-foot { margin-top: 34px; padding-top: 16px; border-top-color: var(--line); font-size: 11.5px; }
  /* 登录弹层 */
  #login { background: rgba(4, 5, 6, .82); backdrop-filter: blur(10px); }
  .login-card { width: 400px; padding: 26px; border-radius: 10px; background: #0d0e10;
    border-color: #26282c; box-shadow: 0 24px 80px rgba(0, 0, 0, .7); }
  .login-mark { width: 32px; height: 32px; margin-bottom: 16px; border-radius: 8px; font-size: 17px;
    border-color: rgba(78, 211, 184, .28); background: rgba(78, 211, 184, .08); }
  .login-card h1 { font-size: 22px; font-weight: 600; letter-spacing: -.02em; }
  .login-subtitle { margin-bottom: 18px; font-size: 12.5px; }
  .login-card label { margin: 12px 0 6px; font-size: 12.5px; color: #a8aeb5; }
  .login-card button { margin-top: 18px; padding: 8px; }
  .login-card .muted { margin-top: 18px; padding-top: 14px; font-size: 11.5px; }
  .login-msg { font-size: 12.5px; }
  /* 细滚动条 */
  .table-scroll, #landing { scrollbar-width: thin; scrollbar-color: #2b2e33 transparent; }
  .table-scroll::-webkit-scrollbar, #landing::-webkit-scrollbar { width: 8px; height: 8px; }
  .table-scroll::-webkit-scrollbar-thumb, #landing::-webkit-scrollbar-thumb {
    background: #2b2e33; border-radius: 999px; }
  .table-scroll::-webkit-scrollbar-track, #landing::-webkit-scrollbar-track { background: transparent; }

  @media (min-width: 768px) and (max-width: 1199px) {
    .page { grid-template-columns: 186px minmax(0, 1fr); }
    .sidebar { padding: 16px 8px 12px; } .brand { font-size: 15px; padding: 0 8px; }
    .tab { padding: 6px 8px; gap: 8px; font-size: 13px; }
    .workspace { padding: 0 24px 24px; }
    .stats { gap: 10px; } .stat { padding: 12px 14px; } .stat .v { font-size: 23px; }
    .landing-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .landing-flow { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  }
  @media (max-width: 767px) {
    .page { display: block; } .sidebar { position: static; height: auto; padding: 20px 16px 14px;
      border-right: 0; border-bottom: 1px solid var(--line); }
    .brand { padding: 0; font-size: 16px; } .brand small { display: inline; margin-left: 10px; font-size: 10.5px; letter-spacing: .1em; }
    .nav-caption, .sidebar-foot { display: none; }
    .tabs { flex-direction: row; flex-wrap: wrap; gap: 6px; margin-top: 18px; }
    .tab { padding: 8px 10px; font-size: 13px; gap: 7px; } .tab::before { font-size: 11px; }
    .workspace { padding: 0 16px 20px; } header { min-height: 52px; gap: 10px; }
    .top-controls { gap: 10px; } .top-label { font-size: 12.5px; } .top-label span { margin-left: 6px; }
    .gateway-hint { margin-top: 12px; padding: 9px 12px; }
    .page-heading { padding: 20px 0 14px; } .page-heading h1 { font-size: 20px; }
    .form, .runtime { grid-template-columns: minmax(0, 1fr); }
    .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-bottom: 14px; }
    .stat { padding: 12px 14px; } .stat .v { font-size: 22px; }
    .card { padding: 16px; } .card.wide, .replay { margin-top: 16px; }
    details.card > summary { padding: 12px 14px; }
    details.card > .card-body { padding: 0 14px 14px; }
    details.card > summary .summary-meta { display: block; margin: 3px 0 0; }
    th, td { padding: 8px 10px; } .form { gap: 14px; }
    .checks { grid-template-columns: minmax(0, 1fr); } fieldset { padding: 14px; }
    .rrow { grid-template-columns: 60px minmax(0, 1fr); }
    .login-card { padding: 22px; } .login-card h1 { font-size: 20px; }
    .landing-shell { padding: 20px 16px 34px; }
    .landing-hero { grid-template-columns: minmax(0, 1fr); gap: 16px; padding: 30px 0 30px; }
    .landing-visual { min-height: 250px; order: -1; }
    .orbit { width: 224px; height: 224px; }
    .orbit-ring.r1 { width: 100px; height: 100px; }
    .orbit-ring.r2 { width: 152px; height: 152px; }
    .orbit-ring.r3 { width: 208px; height: 208px; }
    .landing-glow { width: 190px; height: 190px; }
    .landing-facts { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 24px; }
    .landing-grid { grid-template-columns: minmax(0, 1fr); }
    .review-grid { grid-template-columns: minmax(0, 1fr); }
    .landing-flow { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .landing-section { padding-top: 34px; }
    .landing-section-head h2 { font-size: 18px; }
    .landing-nav-right { width: 100%; justify-content: space-between; }
    .hotl-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .perm-row { grid-template-columns: 64px minmax(0, 1fr); }
    .perm-row > :last-child { grid-column: 1 / -1; }
    .chain-step { grid-template-columns: minmax(0, 1fr); gap: 4px; }
  }
  /* ============================================================
     Linear 式单侧边栏：固定模块 + 任务组 + 底部身份 / L3 主内容
     ============================================================ */
  .app { position: relative; z-index: 1; display: flex; align-items: flex-start; min-height: 100vh; }
  .side { flex: 0 0 auto; width: var(--sidew, 252px); position: sticky; top: 0; height: 100vh;
    overflow-y: auto; z-index: 56; display: flex; flex-direction: column; min-width: 0;
    background: #0a0b0d; border-right: 1px solid var(--line); }
  .side-brand { flex: 0 0 44px; display: flex; align-items: center; gap: 9px; padding: 0 8px 0 12px;
    border-bottom: 1px solid #17181b; }
  .side-brand .mark { width: 24px; height: 24px; flex: 0 0 auto; display: grid; place-items: center;
    border: 1px solid #2a2d32; border-radius: 6px; color: var(--blue); font-size: 12px; font-weight: 700; }
  .side-brand .name { min-width: 0; font-size: 13px; font-weight: 600; color: #f2f4f6; white-space: nowrap; }
  .side-brand .name small { margin-left: 5px; color: #6d727a; font-size: 11px; font-weight: 500; }
  .side-brand .icon-btn { margin-left: auto; padding: 2px 6px; font-size: 11.5px; }
  .side-scroll { flex: 1; overflow-y: auto; padding: 8px 8px 14px; }
  .side-block { display: grid; grid-template-columns: 18px minmax(0, 1fr) auto; align-items: center; gap: 9px;
    width: 100%; min-height: 32px; padding: 0 8px; border: 1px solid transparent; border-radius: 6px;
    background: transparent; color: #c8cdd3; font-size: 13px; text-align: left; cursor: pointer; }
  .side-block svg { width: 16px; height: 16px; }
  .side-block:hover { background: rgba(255, 255, 255, .05); color: #f0f2f4; }
  .side-block.active { background: rgba(255, 255, 255, .08); color: #fff; }
  .side-block .caret { color: #6d727a; font-size: 10px; transition: transform .15s ease; }
  .side-block[aria-expanded="true"] .caret { transform: rotate(90deg); }
  .side-sub { margin: 2px 0 6px 17px; padding-left: 9px; border-left: 1px solid #1d1f23; }
  .side-sub-row { display: flex; align-items: center; gap: 8px; width: 100%; padding: 5px 8px;
    border: 1px solid transparent; border-radius: 5px; background: transparent;
    color: #9aa0a8; font-size: 12.5px; text-align: left; cursor: pointer; }
  .side-sub-row:hover { background: rgba(255, 255, 255, .04); color: #e8ebee; }
  .side-sub-row.active { background: rgba(255, 255, 255, .07); color: #fff; }
  .side-sub-row .side-count { margin-left: auto; min-width: 16px; padding: 0 4px; border-radius: 8px;
    background: rgba(240, 115, 111, .16); color: #f0938f; font-size: 10px; line-height: 16px; text-align: center; }
  .side-group { margin-top: 14px; }
  .side-group-head { display: flex; align-items: center; gap: 2px; padding: 0; }
  .side-group-toggle { flex: 1; min-width: 0; display: grid;
    grid-template-columns: 18px minmax(0, 1fr) auto auto; align-items: center; gap: 9px;
    min-height: 32px; padding: 0 7px; border: 1px solid transparent; border-radius: 6px;
    background: transparent; color: #c8cdd3; font-size: 13px; text-align: left; cursor: pointer; }
  .side-group-toggle svg { width: 16px; height: 16px; }
  .side-group-toggle:hover { background: rgba(255, 255, 255, .05); color: #f0f2f4; }
  .side-group-toggle .count { color: #5b6168; font-size: 10.5px; }
  .side-group-toggle .caret { color: #6d727a; font-size: 10px; transition: transform .15s ease; }
  .side-group-toggle[aria-expanded="true"] .caret { transform: rotate(90deg); }
  .side-add { margin-left: 2px; padding: 0 6px; border: 1px solid #24272b; border-radius: 4px; background: transparent;
    color: #8b929a; font-size: 11px; line-height: 16px; cursor: pointer; }
  .side-add:hover { border-color: #3a3f45; color: #e6e9ec; }
  .side-item { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 8px;
    width: 100%; padding: 6px 8px; border: 1px solid transparent; border-radius: 6px; background: transparent;
    color: #c3c8ce; font-size: 12.5px; text-align: left; cursor: pointer; }
  .side-item:hover { background: rgba(255, 255, 255, .04); color: #eef0f2; }
  .side-item.active { background: rgba(78, 211, 184, .08); color: #fff; }
  .side-item .dot { width: 6px; height: 6px; border-radius: 50%; }
  .side-item .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .side-item .meta { color: #6f757d; font-size: 10.5px; white-space: nowrap; }
  .side-item .meta b { color: #f0938f; font-weight: 600; }
  .side-empty { padding: 6px 8px; color: #5f656d; font-size: 11.5px; line-height: 1.6; }
  .side-foot { flex: 0 0 auto; padding: 8px; border-top: 1px solid #17181b; }
  .side-user { display: grid; grid-template-columns: 26px minmax(0, 1fr) auto; align-items: center; gap: 9px;
    width: 100%; padding: 6px 8px; border: 1px solid transparent; border-radius: 6px;
    background: transparent; color: #c8cdd3; text-align: left; cursor: pointer; }
  .side-user:hover { background: rgba(255, 255, 255, .05); }
  .side-user.active { background: rgba(255, 255, 255, .08); }
  .side-user .avatar { width: 26px; height: 26px; display: grid; place-items: center; border-radius: 50%;
    border: 1px solid #2a2d32; background: #1a1d21; color: #cfd4d9; font-size: 11px; font-weight: 600; }
  .side-user .who { min-width: 0; display: flex; flex-direction: column; line-height: 1.35; }
  .side-user .who b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    color: #e7eaed; font-size: 12.5px; font-weight: 600; }
  .side-user .who span { color: #6f757d; font-size: 10.5px; }
  .side-user .gear { color: #6f757d; font-size: 13px; }
  .side-resizer { position: absolute; top: 0; right: -2px; width: 5px; height: 100%;
    cursor: col-resize; z-index: 2; }
  .side-resizer:hover { background: rgba(78, 211, 184, .22); }
  .side-scrim { position: fixed; inset: 0; z-index: 54; background: rgba(0, 0, 0, .45); }
  tbody tr.active-row { background: rgba(78, 211, 184, .07); }
  .l2-empty { padding: 14px 12px; color: #6d727a; font-size: 12px; }
  .l2-hint { padding: 8px 12px 0; color: #6d727a; font-size: 11px; line-height: 1.6; }
  /* Agent 工作台 */
  .agent-hero { display: flex; align-items: flex-end; justify-content: space-between; gap: 18px;
    padding: 4px 0 16px; border-bottom: 1px solid var(--line); }
  .agent-hero h2 { margin: 2px 0 4px; font-size: 20px; font-weight: 600; }
  .agent-hero .agent-hero-meta { color: #8b9098; font-size: 12.5px; line-height: 1.7; }
  .agent-tabs { display: flex; gap: 2px; padding: 10px 0 0; border-bottom: 1px solid var(--line); }
  .agent-tab { padding: 7px 12px; border: 0; border-bottom: 2px solid transparent; border-radius: 0;
    background: transparent; color: #8b9098; font-size: 13px; cursor: pointer; }
  .agent-tab:hover { color: #e4e6e8; }
  .agent-tab.active { border-bottom-color: var(--green); color: #f7f8f8; }
  .agent-body { padding-top: 16px; }
  .agent-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-bottom: 16px; }
  .agent-stat { padding: 14px; border: 1px solid var(--line); border-radius: 7px; background: #0d0e10; }
  .agent-stat .k { color: #7f858d; font-size: 11px; }
  .agent-stat .v { margin-top: 6px; font-size: 20px; font-weight: 600; }
  .agent-stat .s { margin-top: 2px; color: #6f757d; font-size: 10.5px; }
  .agent-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  .agent-form-note { padding: 10px 12px; border: 1px dashed #26292e; border-radius: 7px;
    color: #8b9098; font-size: 12px; line-height: 1.7; }
  .task-list { display: flex; flex-direction: column; gap: 6px; }
  .task-row { display: grid; grid-template-columns: auto minmax(0, 1fr) 120px 80px; gap: 10px;
    align-items: center; width: 100%; padding: 9px 12px; border: 1px solid var(--line);
    border-radius: 6px; background: #0d0e10; color: #d6d9dd; font-size: 12.5px; text-align: left; cursor: pointer; }
  .task-row:hover { border-color: #33373d; background: #101114; }
  .task-row .meta { color: #7f858d; font-size: 11.5px; text-align: right; }
  .task-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px;
    padding: 4px 0 16px; border-bottom: 1px solid var(--line); }
  .task-head h2 { margin: 2px 0 4px; font-size: 19px; font-weight: 600; }
  .task-body { padding-top: 16px; }
  .account-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; padding-top: 16px; }
  .account-grid .card { margin: 0; }
  .kv { display: flex; flex-direction: column; gap: 6px; margin: 4px 0 12px; font-size: 12.5px; }
  .kv div { display: flex; justify-content: space-between; gap: 10px; }
  .kv span { color: #7f858d; }
  @media (max-width: 1100px) {
    .account-grid { grid-template-columns: minmax(0, 1fr); }
    .agent-grid { grid-template-columns: minmax(0, 1fr); }
    .agent-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
  /* L3：主内容 + 44px 工具条 */
  .l3 { flex: 1 1 auto; min-width: 0; background: var(--bg); }
  .toolbar { flex: 0 0 44px; height: 44px; display: flex; align-items: center; gap: 10px;
    padding: 0 14px; border-bottom: 1px solid var(--line); background: #0b0c0e; }
  .crumb { display: flex; align-items: center; gap: 6px; min-width: 0; font-size: 12.5px; color: #8b9098; }
  .crumb b { color: #f7f8f8; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .crumb button { padding: 1px 6px; font-size: 11.5px; }
  .tb-center { display: flex; gap: 2px; flex: 0 0 auto; }
  .tb-seg { padding: 3px 9px; font-size: 12px; border: 1px solid transparent; border-radius: 5px;
    background: transparent; color: #8b9098; }
  .tb-seg:hover { background: rgba(255, 255, 255, .04); color: #d6d9dd; }
  .tb-seg.active { background: rgba(255, 255, 255, .07); color: #f7f8f8; }
  button.run-item, button.palette-item, button.tb-seg, button.side-item, button.side-block { border-color: transparent; }
  button.run-item:hover, button.palette-item:hover, button.side-item:hover, button.side-block:hover {
    background: rgba(255, 255, 255, .04); border-color: transparent; }
  button.run-item.active, button.side-item.active, button.side-block.active { background: rgba(255, 255, 255, .07); }
  .tb-nav { flex: 0 0 auto; width: 28px; height: 28px; padding: 0; display: grid; place-items: center;
    border-color: transparent; background: transparent; color: #9aa0a8; }
  button.tb-nav:hover { background: rgba(255, 255, 255, .06); border-color: transparent; color: #f7f8f8; }
  .tb-right { margin-left: auto; display: flex; align-items: center; gap: 8px; }
  .tb-right .pill { flex: 0 0 auto; }
  kbd { padding: 0 4px; border: 1px solid #2a2d32; border-bottom-width: 2px; border-radius: 4px;
    background: #141519; color: #9aa0a8; font-family: inherit; font-size: 10.5px; }
  .l3-body { padding: 0 20px 24px; }
  .l3-body .page-heading { display: none; }
  .l3-body .panel.active { display: block; padding-top: 16px; }
  /* 运行列表（时间线 / 表格 / 按 Agent 分组） */
  .run-view { display: none; }
  .run-view.active { display: block; }
  .run-list { border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
  .run-day, .run-agent-group { padding: 8px 12px 6px; color: #6d727a; font-size: 11px;
    background: #0b0c0e; border-top: 1px solid #17181b; }
  .run-list .run-day:first-child { border-top: 0; }
  .run-item { display: grid; grid-template-columns: auto minmax(0, 1fr) 130px 74px 64px; gap: 10px;
    align-items: center; width: 100%; min-height: 34px; padding: 6px 12px; border: 0; border-top: 1px solid #17181b;
    border-radius: 0; background: transparent; color: #d6d9dd; font-size: 13px; text-align: left; }
  .run-item:hover { background: rgba(255, 255, 255, .04); }
  .run-item.active { background: rgba(78, 211, 184, .07); }
  .run-item .agent, .run-item .time, .run-item .pending { color: #7f858d; font-size: 11.5px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .run-item .time, .run-item .pending { text-align: right; }
  .run-item .pending.hot { color: #f0938f; }
  /* 右侧抽屉：480px，滑出覆盖，不挤压主区 */
  .drawer-scrim { position: fixed; inset: 0; z-index: 60; background: rgba(3, 4, 5, .5); }
  .drawer { position: fixed; top: 0; right: 0; bottom: 0; width: 480px; max-width: 100%;
    display: flex; flex-direction: column; background: #0c0d0f; border-left: 1px solid var(--line);
    z-index: 61; transform: translateX(100%); transition: transform .18s ease; }
  .drawer.open { transform: none; }
  .drawer-head { flex: 0 0 44px; height: 44px; display: flex; align-items: center; justify-content: space-between;
    gap: 10px; padding: 0 14px; border-bottom: 1px solid var(--line); font-size: 12.5px; font-weight: 600; }
  .drawer-body { flex: 1; overflow: auto; padding: 14px; font-size: 12.5px; }
  .drawer-sec { margin-bottom: 18px; }
  .drawer-label { margin-bottom: 6px; color: #6d727a; font-size: 10.5px; letter-spacing: .08em; }
  .drawer-body pre { max-height: 420px; }
  .kv { display: grid; grid-template-columns: 92px minmax(0, 1fr); gap: 6px 10px; font-size: 12px; }
  .kv .k { color: #7f858d; }
  /* 全局命令面板 */
  .palette-scrim { position: fixed; inset: 0; z-index: 70; background: rgba(3, 4, 5, .6); }
  .palette { position: fixed; top: 11vh; left: 50%; transform: translateX(-50%); width: 580px;
    max-width: calc(100vw - 24px); max-height: 64vh; display: flex; flex-direction: column;
    background: #0c0d0f; border: 1px solid #2a2d32; border-radius: 8px; z-index: 71;
    box-shadow: 0 24px 80px rgba(0, 0, 0, .6); }
  .palette input { border: 0; border-bottom: 1px solid var(--line); border-radius: 0; background: transparent;
    padding: 12px 14px; font-size: 13.5px; color: #f7f8f8; }
  .palette input:focus { box-shadow: none; }
  .palette-list { overflow: auto; padding: 6px; }
  .palette-group { padding: 6px 10px 2px; color: #6d727a; font-size: 10.5px; letter-spacing: .08em; }
  .palette-item { display: flex; align-items: center; gap: 10px; width: 100%; padding: 7px 10px;
    border: 0; border-radius: 5px; background: transparent; color: #d6d9dd; font-size: 12.5px; text-align: left; }
  .palette-item.active { background: rgba(78, 211, 184, .1); color: #fff; }
  .palette-item .hint { margin-left: auto; color: #6d727a; font-size: 10.5px; }
  /* 审批详情 */
  .apd-grid { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, .9fr); gap: 14px; }
  .apd-grid pre { margin: 0; max-height: 260px; }
  .apd-impact { display: grid; gap: 6px; font-size: 12px; }
  .apd-impact .row { display: flex; justify-content: space-between; gap: 10px;
    padding: 5px 0; border-bottom: 1px solid #17181b; }
  .apd-impact .row:last-child { border-bottom: 0; }
  .apd-count { color: #f0938f; font-variant-numeric: tabular-nums; }
  /* 使用导航：整页引导（低对比、无盒子，参照 Linear 的列表感） */
  .guide-hero { position: relative; display: grid;
    grid-template-columns: minmax(0, 1fr) 150px; gap: 24px; align-items: center;
    padding: 4px 0 20px; margin-bottom: 6px; border: 0; border-bottom: 1px solid #1b1d21;
    border-radius: 0; background: transparent; }
  .guide-hero h2 { margin: 4px 0 8px; font-size: 21px; font-weight: 600; letter-spacing: -.02em; }
  .guide-hero p { margin: 0; max-width: 68ch; color: #8b9199; font-size: 12.5px; line-height: 1.75; }
  .guide-hero-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
  .guide-next { margin-top: 12px; padding: 2px 0 2px 10px; border-left: 2px solid rgba(78, 211, 184, .35);
    color: #94a6a1; font-size: 12px; }
  .guide-progress { position: relative; display: grid; place-items: center; }
  .guide-ring { width: 104px; height: 104px; transform: rotate(-90deg); }
  .guide-ring circle { fill: none; stroke-width: 4; }
  .guide-ring .ring-track { stroke: rgba(255, 255, 255, .06); }
  .guide-ring .ring-value { stroke: var(--blue); stroke-linecap: round;
    stroke-dasharray: 326.7; stroke-dashoffset: 326.7; transition: stroke-dashoffset .5s ease; }
  .guide-progress-text { position: absolute; text-align: center; }
  .guide-progress-text b { display: block; font-size: 16px; font-weight: 600; letter-spacing: -.02em; }
  .guide-progress-text span { color: #6f757d; font-size: 10px; }
  .guide-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0;
    margin-bottom: 20px; border-bottom: 1px solid #1b1d21; }
  .guide-metric { display: flex; flex-direction: column; gap: 1px; padding: 12px 0 14px 16px;
    text-align: left; border: 0; border-radius: 0; background: transparent; }
  .guide-metric:first-child { padding-left: 0; }
  .guide-metric + .guide-metric { border-left: 1px solid #1b1d21; }
  .guide-metric:hover, button.guide-metric:hover { background: transparent; border-color: #1b1d21; }
  button.guide-metric { border: 0; border-radius: 0; background: transparent; }
  .guide-metric .k { color: #7f858d; font-size: 10.5px; }
  .guide-metric b { font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .guide-metric .s { color: #6d727a; font-size: 10.5px; }
  .guide-section { margin-bottom: 20px; }
  .guide-section-head { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; margin-bottom: 2px; }
  .guide-section-head h3 { margin: 0; font-size: 12px; font-weight: 600; color: #a9aeb5; }
  .guide-cards { display: grid; grid-template-columns: minmax(0, 1fr); gap: 0; }
  .guide-card { display: flex; flex-direction: column; padding: 13px 0; border: 0;
    border-bottom: 1px solid #1b1d21; border-radius: 0; background: transparent; }
  .guide-card-top { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  .guide-num { font: 10.5px "Cascadia Code", Consolas, monospace; letter-spacing: .1em; color: #5f656d; }
  .guide-state { font-size: 10.5px; color: #6f757d; }
  .guide-card.done { border-color: #1b1d21; }
  .guide-card.done .guide-state { color: #6fd7b6; }
  .guide-card.done .guide-num { color: #6fd7b6; }
  .guide-card h4 { margin: 3px 0 3px; font-size: 13px; font-weight: 600; }
  .guide-card p { margin: 0 0 8px; color: #8b9199; font-size: 12px; line-height: 1.65; }
  .guide-card-actions { margin-top: auto; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .guide-code { display: flex; align-items: center; gap: 8px; margin: 0 0 8px; }
  .guide-code code { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
  .guide-paths { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0;
    border-top: 1px solid #1b1d21; }
  .guide-path { padding: 12px 16px 2px 0; border: 0; border-radius: 0; background: transparent; }
  .guide-path + .guide-path { padding-left: 16px; border-left: 1px solid #1b1d21; }
  .guide-path h4 { margin: 0 0 3px; font-size: 12.5px; font-weight: 600; }
  .guide-path p { margin: 0 0 8px; color: #8b9199; font-size: 12px; line-height: 1.65; }
  @media (max-width: 1023px) {
    .guide-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .guide-paths { grid-template-columns: minmax(0, 1fr); }
  }
  @media (max-width: 767px) {
    .guide-hero { grid-template-columns: minmax(0, 1fr); padding: 22px 18px; }
    .guide-progress { display: none; }
    .guide-cards { grid-template-columns: minmax(0, 1fr); }
  }
  /* ============================================================
     极简层：低对比、少盒子、多留白（参照 Linear 的阅读舒适度）
     ============================================================ */
  .bg-ambient { opacity: .3; }
  .card, details.card { background: transparent; border-color: #1b1d21; box-shadow: none; }
  .card { padding: 16px; }
  details.card > summary { background: transparent; }
  details.card > summary:hover { background: rgba(255, 255, 255, .02); }
  .stat, .agent-stat { background: transparent; border-color: #1b1d21; }
  .stats { gap: 0; border-bottom: 1px solid #1b1d21; margin-bottom: 18px; }
  .stats .stat { border: 0; border-left: 1px solid #1b1d21; border-radius: 0; padding: 2px 0 14px 16px; }
  .stats .stat:first-child { border-left: 0; padding-left: 0; }
  .stats .stat .v { font-size: 22px; }
  th { background: transparent; }
  th, td { border-bottom-color: #1b1d21; }
  tbody tr:hover { background: rgba(255, 255, 255, .025); }
  .toolbar { background: #0b0c0e; border-bottom-color: #1b1d21; }
  .l3-body { padding: 0 26px 30px; }
  .l3-body .panel.active { padding-top: 12px; }
  .agent-hero, .task-head { border-bottom-color: #1b1d21; }
  .agent-hero h2, .task-head h2 { font-size: 18px; }
  .agent-tab.active { border-bottom-color: #4ec9a5; }
  .agent-stats { gap: 0; border-bottom: 1px solid #1b1d21; }
  .agent-stat { border: 0; border-left: 1px solid #1b1d21; border-radius: 0; padding: 2px 0 14px 16px; }
  .agent-stat:first-child { border-left: 0; padding-left: 0; }
  .agent-stat .v { font-size: 19px; }
  .task-row { border: 0; border-bottom: 1px solid #1b1d21; border-radius: 0; background: transparent; }
  .task-row:hover { background: rgba(255, 255, 255, .025); border-color: #1b1d21; }
  .account-grid .card { border: 0; border-top: 1px solid #1b1d21; border-radius: 0; padding: 14px 0 0; }
  .side-scroll { padding: 6px 6px 12px; }
  .side-block { min-height: 28px; padding: 0 7px; font-size: 12.5px; }
  .side-sub-row { padding: 4px 7px; font-size: 12px; }
  .side-group { margin-top: 15px; }
  .side-group-head { padding: 0; }
  .side-group-toggle { min-height: 28px; padding: 0 7px; font-size: 12.5px; }
  .side-item { padding: 5px 7px; font-size: 12.5px; }
  .side-item .meta { color: #5f656d; font-size: 10px; }
  .side-foot { padding: 6px; }
  .side-user { padding: 5px 7px; }
  @media (min-width: 1024px) {
    #user-label, #logout { display: none !important; }
  }
  /* 响应式：窄屏侧边栏变为浮层 */
  @media (max-width: 1023px) {
    .app { display: block; }
    .side { position: fixed; top: 0; left: 0; bottom: auto; height: 100vh;
      width: 276px; z-index: 55; transform: translateX(-100%); transition: transform .18s ease; }
    .side.overlay-open { transform: none; }
    .side-resizer { display: none; }
    .l3-body { padding: 0 14px 20px; }
    .apd-grid { grid-template-columns: minmax(0, 1fr); }
  }
  @media (min-width: 1024px) {
    .side.collapsed { display: none; }
  }
  @media (max-width: 1023px) {
    .tb-right { gap: 6px; }
    .tb-right .pill, #tb-export { display: none; }
  }
  @media (max-width: 767px) {
    .tb-center { display: none; }
    .tb-right .pill, #tb-export { display: none; }
    .drawer { width: 100%; }
  }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { transition: none !important; animation: none !important; }
  }
</style>
</head>
<body>
<div class="bg-ambient" aria-hidden="true"><div class="bg-halo"></div><div class="bg-scan"></div></div>
<div class="app" id="app">
  <aside class="side" id="side" aria-label="主导航">
    <div class="side-brand">
      <span class="mark" aria-hidden="true">S</span>
      <span class="name">SafeOrbit<small>安轨</small></span>
      <button class="icon-btn" id="side-collapse" type="button" aria-label="折叠侧边栏" title="折叠侧边栏">«</button>
    </div>
    <div class="side-scroll" id="side-body">
      <div class="side-fixed" id="side-fixed">
        <button class="side-block active" type="button" id="side-guide-toggle" data-module="guide">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="m15 9-1.9 4.1L9 15l1.9-4.1z"/></svg>
          <span>使用导航</span>
        </button>

        <button class="side-block" type="button" id="side-agents-toggle" data-module="agents">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="6" y="7.5" width="12" height="9.5" rx="2"/><path d="M9 7.5V5M15 7.5V5M12 17v2.5"/></svg>
          <span>Agent 管理</span>
        </button>

        <button class="side-block" type="button" id="side-models-toggle" data-module="models">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="3.2"/><path d="M12 3.4v3M12 17.6v3M3.4 12h3M17.6 12h3M6 6l2.1 2.1M15.9 15.9 18 18M18 6l-2.1 2.1M8.1 15.9 6 18"/></svg>
          <span>系统设置</span>
        </button>
      </div>
      <div id="side-groups"></div>
    </div>
    <div class="side-foot">
      <button class="side-user" type="button" id="side-user" data-module="account" aria-label="设置、切换身份与登出">
        <span class="avatar" id="side-avatar" aria-hidden="true">S</span>
        <span class="who"><b id="side-user-name">未登录</b><span id="side-user-role">设置 · 身份</span></span>
        <span class="gear" aria-hidden="true">⚙</span>
      </button>
    </div>
    <div class="side-resizer" id="side-resizer" title="拖动调整侧边栏宽度"></div>
  </aside>
  <div class="side-scrim" id="side-scrim" hidden></div>

  <main class="l3">
    <div class="toolbar">
      <button class="small tb-nav" id="tb-nav" type="button" aria-label="切换导航" title="切换导航">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M4 6.5h16M4 12h16M4 17.5h16"/></svg>
      </button>
      <div class="crumb" id="crumb"></div>
      <div class="tb-center" id="tb-views" hidden>
        <button class="tb-seg active" type="button" data-view="timeline">时间线</button>
        <button class="tb-seg" type="button" data-view="table">表格</button>
        <button class="tb-seg" type="button" data-view="agent">按 Agent 分组</button>
      </div>
      <div class="tb-right">
        <button class="small" id="tb-search" type="button" aria-label="全局搜索">搜索 <kbd>⌘K</kbd></button>
        <button class="small" id="tb-filter" type="button">筛选</button>
        <button class="small" id="tb-export" type="button">导出</button>
        <div class="pill"><span class="dot"></span><span id="mode">连接中…</span></div>
        <span id="user-label" class="muted"></span>
        <button id="logout" class="small" type="button">退出</button>
      </div>
    </div>
    <div class="l3-body" id="l3-body">

  <!-- 使用导航 -->
  <div class="panel active" id="panel-guide">
    <section class="guide-hero">
      <div>
        <div class="eyebrow">GUIDE / 使用导航</div>
        <h2>欢迎使用 SafeOrbit 安轨</h2>
        <p>从接入一个智能体，到看清它的每一步、拦住危险操作。按下面五步走，十几分钟就能跑通；每一步都可以直接跳到对应页面。</p>
        <div class="guide-hero-actions">
          <button class="primary" type="button" id="guide-primary" data-guide-go="agents">继续下一步</button>
          <button type="button" data-guide-go="runs">看看现有运行</button>
        </div>
        <div class="guide-next" id="guide-next">建议从第 1 步开始：注册智能体并签发 Key。</div>
      </div>
      <div class="guide-progress" aria-label="引导完成进度">
        <svg viewBox="0 0 120 120" class="guide-ring" aria-hidden="true">
          <circle cx="60" cy="60" r="52" class="ring-track"></circle>
          <circle cx="60" cy="60" r="52" class="ring-value" id="guide-ring-value"></circle>
        </svg>
        <div class="guide-progress-text"><b id="guide-progress-count">0/4</b><span>已完成</span></div>
      </div>
    </section>

    <div class="guide-metrics">
      <button type="button" class="guide-metric" data-guide-go="agents"><span class="k">智能体</span><b id="guide-count-agents">0</b><span class="s">已注册</span></button>
      <button type="button" class="guide-metric" data-guide-go="runs"><span class="k">运行记录</span><b id="guide-count-runs">0</b><span class="s">已审计</span></button>
      <button type="button" class="guide-metric" data-guide-go="approvals"><span class="k">待审批</span><b id="guide-count-approvals">0</b><span class="s">等待处理</span></button>
      <button type="button" class="guide-metric" data-guide-go="policies"><span class="k">策略</span><b id="guide-count-policies">0</b><span class="s">已配置</span></button>
    </div>

    <section class="guide-section">
      <div class="guide-section-head"><h3>五步走通</h3><span class="muted">完成的步骤会自动打勾</span></div>
      <div class="guide-cards">
        <article class="guide-card" id="guide-step-agents">
          <div class="guide-card-top"><span class="guide-num">01</span><span class="guide-state" id="guide-state-agents">待完成</span></div>
          <h4>注册智能体并签发 Key</h4>
          <p>在「Agent 管理」里注册 Agent，签发一次性 Key；Key 只展示一次，请放进环境变量。</p>
          <div class="guide-card-actions"><button class="small" type="button" data-guide-go="agents">去接入</button></div>
        </article>
        <article class="guide-card" id="guide-step-demo">
          <div class="guide-card-top"><span class="guide-num">02</span><span class="guide-state">命令 / 模板</span></div>
          <h4>跑一次演示或接入 SDK</h4>
          <p>离线注入三条演示轨迹：越权退款、提示注入、RAG 投毒；也可以套用接入模板。</p>
          <div class="guide-code"><code>py -m safeorbit demo</code><button class="small" type="button" id="guide-copy-demo">复制</button></div>
          <div class="guide-card-actions"><button class="small" type="button" data-guide-go="agents">查看接入模板</button></div>
        </article>
        <article class="guide-card" id="guide-step-runs">
          <div class="guide-card-top"><span class="guide-num">03</span><span class="guide-state" id="guide-state-runs">待完成</span></div>
          <h4>查看运行与证据</h4>
          <p>点开一条运行，左侧是事件时间线；点任意步骤，右侧抽屉展示输入输出、来源与权限。</p>
          <div class="guide-card-actions"><button class="small" type="button" data-guide-go="runs">打开运行记录</button></div>
        </article>
        <article class="guide-card" id="guide-step-approvals">
          <div class="guide-card-top"><span class="guide-num">04</span><span class="guide-state" id="guide-state-approvals">待完成</span></div>
          <h4>处理需要人工确认的操作</h4>
          <p>写操作会先生成审批单；高风险必须填写理由，批准后的票据一次性有效。</p>
          <div class="guide-card-actions"><button class="small" type="button" data-guide-go="approvals">打开审批队列</button></div>
        </article>
        <article class="guide-card" id="guide-step-policies">
          <div class="guide-card-top"><span class="guide-num">05</span><span class="guide-state" id="guide-state-policies">待完成</span></div>
          <h4>配置策略与边界</h4>
          <p>先用「仅记录」观察，再对单个 Agent 开启「允许阻断」；可设置白名单、预算与金额阈值。</p>
          <div class="guide-card-actions"><button class="small" type="button" data-guide-go="policies">打开策略配置</button></div>
        </article>
      </div>
    </section>

    <section class="guide-section">
      <div class="guide-section-head"><h3>按你的情况选择</h3></div>
      <div class="guide-paths">
        <article class="guide-path">
          <h4>我是第一次接入</h4>
          <p>先注册 Agent 并签发 Key，再套用 SDK 模板。</p>
          <button class="small" type="button" data-guide-go="agents">从接入开始</button>
        </article>
        <article class="guide-path">
          <h4>我已经有运行数据</h4>
          <p>直接看时间线与五视图，核对证据链是否完整。</p>
          <button class="small" type="button" data-guide-go="runs">打开运行记录</button>
        </article>
        <article class="guide-path">
          <h4>我准备上线</h4>
          <p>配置策略、开启人工审批，再逐步切到拦截模式。</p>
          <button class="small" type="button" data-guide-go="policies">配置策略</button>
        </article>
      </div>
    </section>
  </div>

  <!-- 总览 -->
  <div class="panel" id="panel-overview">
    <div class="stats">
      <div class="stat"><div class="k">已审计记录</div><div class="v" id="stat-total">0</div></div>
      <div class="stat"><div class="k">不通过</div><div class="v" id="stat-fail" style="color:var(--red)">0</div></div>
      <div class="stat"><div class="k">需要关注</div><div class="v" id="stat-warn" style="color:var(--amber)">0</div></div>
      <div class="stat"><div class="k">累计模型成本</div><div class="v" id="stat-cost">$0.0000</div></div>
    </div>
    <div class="first-run">第一次用？按这个顺序看就行：① 展开「最近记录」，点一行查看它做了什么 ② 有红色提醒时，到「风险处置」审批或隔离 ③ 想知道整体情况，展开「监管指标 · HOTL」。</div>
    <details class="card" id="hotl-card" style="margin-bottom:22px">
      <summary><span><span class="summary-title">监管指标 · HOTL</span><span class="summary-meta" id="hotl-meta">人在环上：异常指标可一键定位</span></span></summary>
      <div class="card-body">
        <p class="view-note">每个指标都对应一个动作：升级率→风险处置，驳回率与审批时长→审批队列，漂移→待复核记录，成本→汇总报告。</p>
        <div class="hotl-grid">
          <button type="button" class="hotl-item" data-hotl="escalation">
            <div class="k">升级率（自动 → 人工）</div><div class="v" id="hotl-escalation">—</div>
            <div class="s" id="hotl-escalation-sub">风险记录占比</div>
          </button>
          <button type="button" class="hotl-item" data-hotl="rejection">
            <div class="k">驳回率</div><div class="v" id="hotl-rejection">—</div>
            <div class="s" id="hotl-rejection-sub">已决策审批中驳回占比</div>
          </button>
          <button type="button" class="hotl-item" data-hotl="drift">
            <div class="k">漂移信号</div><div class="v" id="hotl-drift">—</div>
            <div class="s" id="hotl-drift-sub">命中基线偏离规则的记录</div>
          </button>
          <button type="button" class="hotl-item" data-hotl="decision">
            <div class="k">平均审批决策时长</div><div class="v" id="hotl-decision">—</div>
            <div class="s" id="hotl-decision-sub">过短说明可能在走过场</div>
          </button>
          <button type="button" class="hotl-item" data-hotl="cost">
            <div class="k">近 7 天成本</div><div class="v" id="hotl-cost">—</div>
            <div class="s" id="hotl-cost-sub">与上一周期对比</div>
          </button>
        </div>
        <div class="hint" id="hotl-note" role="status"></div>
      </div>
    </details>
    <details class="card wide" id="alerts-card">
      <summary><span><span class="summary-title">实时提醒</span><span class="summary-meta" id="alerts-count">暂无提醒</span></span></summary>
      <div class="card-body">
        <div id="alerts"><div class="empty">暂无提醒。发现中风险以上问题时，这里会实时亮起。</div></div>
      </div>
    </details>
  </div>

  <!-- Agent 工作台：每个现有 Agent 的任务入口 -->
  <div class="panel" id="panel-agent">
    <section class="agent-hero">
      <div>
        <div class="eyebrow">AGENT / 工作台</div>
        <h2 id="agent-page-title">智能体</h2>
        <div class="agent-hero-meta" id="agent-page-meta">从左侧「现有 Agent」选择一个智能体。</div>
      </div>
      <div class="agent-hero-actions" id="agent-page-actions"></div>
    </section>
    <div class="agent-tabs" role="tablist" aria-label="Agent 任务类型">
      <button class="agent-tab active" type="button" role="tab" id="agent-tab-total" data-agent-tab="total">任务总计</button>
      <button class="agent-tab" type="button" role="tab" id="agent-tab-records" data-agent-tab="records">检查记录</button>
    </div>
    <div class="agent-body" id="agent-page-body"></div>
  </div>

  <!-- 任务详情：侧边栏任务组里的每个子模块 -->
  <div class="panel" id="panel-task">
    <section class="task-head">
      <div>
        <div class="eyebrow" id="task-kind">TASK / 任务</div>
        <h2 id="task-title">任务详情</h2>
        <div class="muted" id="task-meta"></div>
      </div>
      <div class="actions" style="margin:0">
        <button class="small" type="button" id="task-back">返回 Agent</button>
        <button class="small" type="button" id="task-open-agent">打开 Agent 工作台</button>
      </div>
    </section>
    <div class="task-body" id="task-body"></div>
  </div>

  <!-- 设置：身份切换、注册与登出 -->
  <div class="panel" id="panel-account">
    <section class="task-head">
      <div>
        <div class="eyebrow">SETTINGS / 设置</div>
        <h2>身份与账号</h2>
        <p class="muted" style="margin:4px 0 0">切换身份会用目标账号重新登录本机；新账号由管理员创建，创建后立即生效。</p>
      </div>
    </section>
    <div class="account-grid">
      <div class="card">
        <h2>当前身份</h2>
        <div class="kv" id="account-current"></div>
        <div class="actions"><button id="account-logout" class="small">退出登录</button></div>
      </div>
      <div class="card">
        <h2>切换身份</h2>
        <div class="hint" style="margin-top:0">管理员可全权操作；普通用户只读。切换需输入目标账号密码。</div>
        <div class="form">
          <label class="field">已有账号<select id="account-switch-user"><option value="">手动输入</option></select></label>
          <label class="field">用户名<input id="account-switch-name" placeholder="例如 auditor" autocomplete="username"></label>
          <label class="field">密码<input id="account-switch-pass" type="password" autocomplete="current-password"></label>
        </div>
        <div class="actions">
          <button id="account-switch" class="primary">切换并重新登录</button>
          <span class="muted" id="account-switch-msg" role="status"></span>
        </div>
      </div>
      <div class="card">
        <h2>新注册账号</h2>
        <div class="hint" id="account-register-hint" style="margin-top:0">仅管理员可创建账号。</div>
        <div class="form">
          <label class="field">用户名<input id="account-new-name" placeholder="至少 3 位" autocomplete="off"></label>
          <label class="field">显示名称<input id="account-new-display" placeholder="可选" autocomplete="off"></label>
          <label class="field">初始密码<input id="account-new-pass" type="password" autocomplete="new-password"></label>
          <label class="field">角色
            <select id="account-new-role">
              <option value="viewer">普通用户（只读）</option>
              <option value="auditor">审计员（可审批）</option>
              <option value="admin">管理员（全权）</option>
            </select>
          </label>
        </div>
        <div class="actions">
          <button id="account-register" class="primary">创建账号</button>
          <span class="muted" id="account-register-msg" role="status"></span>
        </div>
      </div>
    </div>
  </div>

  <!-- 运行记录 -->
  <div class="panel" id="panel-traces">
    <details class="card" id="trace-filters" style="margin-bottom:14px">
      <summary><span><span class="summary-title">筛选与导出</span><span class="summary-meta" id="trace-total">按智能体、判定和时间缩小范围</span></span></summary>
      <div class="card-body">
        <div class="form">
          <label class="field">智能体<input id="f-agent" placeholder="例如 cs-agent"></label>
          <label class="field">判定
            <select id="f-verdict">
              <option value="">全部</option>
              <option value="pass">通过</option>
              <option value="warn">有风险</option>
              <option value="fail">不通过</option>
              <option value="review">待复核</option>
            </select>
          </label>
          <label class="field">关键词<input id="f-search" placeholder="记录编号 / 会话编号"></label>
          <label class="field">开始时间<input id="f-start" type="datetime-local"></label>
          <label class="field">结束时间<input id="f-end" type="datetime-local"></label>
        </div>
        <div class="actions">
          <button class="small primary" id="f-apply">应用筛选</button>
          <button class="small" id="f-reset">重置</button>
          <button class="small" id="f-export">导出 CSV</button>
        </div>
      </div>
    </details>
    <details class="card" id="summary" style="margin-bottom:22px">
      <summary><span><span class="summary-title">审计汇总</span><span class="summary-meta" id="summary-scope">全部历史记录</span></span></summary>
      <div class="card-body">
        <div class="actions" style="margin-top:0">
          <button class="small" id="summary-refresh">刷新</button>
          <button class="small primary" id="summary-export" disabled>导出汇总报告</button>
          <a id="summary-latest" target="_blank" rel="noopener" hidden>打开最近汇总报告 →</a>
          <span class="muted" id="summary-msg" role="status"></span>
        </div>
        <details class="fold">
          <summary>判定分布</summary>
          <div class="fold-body">
            <div class="table-scroll" tabindex="0" role="region" aria-label="判定分布表格"><table>
              <thead><tr><th>判定</th><th>数量</th><th>占比</th></tr></thead>
              <tbody id="summary-verdicts"><tr><td colspan="3" class="empty">加载中…</td></tr></tbody>
            </table></div>
          </div>
        </details>
        <details class="fold">
          <summary>智能体风险排名</summary>
          <div class="fold-body">
            <div class="table-scroll" tabindex="0" role="region" aria-label="智能体风险排名表格"><table>
              <thead><tr><th>智能体</th><th>记录</th><th>风险记录</th><th>发现</th><th>成本</th><th>最近</th></tr></thead>
              <tbody id="summary-agents"><tr><td colspan="6" class="empty">加载中…</td></tr></tbody>
            </table></div>
          </div>
        </details>
        <details class="fold">
          <summary>近 14 天趋势</summary>
          <div class="fold-body">
            <div class="table-scroll" tabindex="0" role="region" aria-label="近 14 天趋势表格"><table>
              <thead><tr><th>日期</th><th>记录</th><th>成本</th><th>分布</th></tr></thead>
              <tbody id="summary-trend"><tr><td colspan="4" class="empty">加载中…</td></tr></tbody>
            </table></div>
          </div>
        </details>
        <details class="fold">
          <summary>风险记录明细</summary>
          <div class="fold-body">
            <div class="table-scroll" tabindex="0" role="region" aria-label="风险记录明细表格"><table>
              <thead><tr><th>记录</th><th>智能体</th><th>判定</th><th>命中</th><th>成本</th><th>时间</th><th>命中摘要</th></tr></thead>
              <tbody id="summary-risks"><tr><td colspan="7" class="empty">加载中…</td></tr></tbody>
            </table></div>
          </div>
        </details>
      </div>
    </details>
    <div class="run-view active" id="run-view-timeline">
      <div class="run-list" id="run-list"></div>
      <div class="l2-hint" style="padding-left:0">选择一条运行查看事件时间线；↑↓ 切换，Enter 打开详情。</div>
    </div>
    <div class="run-view" id="run-view-agent">
      <div class="run-list" id="run-agent-list"></div>
    </div>
    <div class="run-view" id="run-view-table">
    <details class="card" id="recent-card">
      <summary><span><span class="summary-title">最近记录</span><span class="summary-meta" id="recent-count">暂无记录</span></span></summary>
      <div class="card-body">
        <div class="table-scroll" tabindex="0" role="region" aria-label="最近记录表格"><table>
          <thead><tr><th>记录</th><th>智能体</th><th>判定</th><th>发现</th><th>成本</th><th>时间</th></tr></thead>
          <tbody id="traces"><tr><td colspan="6" class="empty">等待智能体发来第一条调用…</td></tr></tbody>
        </table></div>
        <div class="actions" style="margin-top:10px">
          <button class="small" id="trace-prev">上一页</button>
          <span class="muted" id="trace-page">第 1 页</span>
          <button class="small" id="trace-next">下一页</button>
        </div>
      </div>
    </details>
    </div>
  </div>

  <!-- 审计视图 -->
  <div class="panel" id="panel-audit">
    <div class="page-heading"><div class="eyebrow">AUDIT VIEW / 03</div><h1>审计视图</h1><p>从「运行记录 → 最近记录」或「现有 Agent → 最近运行」点开任意一条记录，查看该次运行的时间线、资源触碰、权限、决策来源与持久变更。</p></div>
    <div class="card" id="audit-empty" style="margin-bottom:22px">
      <div class="hint" style="margin:0">还没有选择记录。去「运行记录 → 最近记录」或左侧「现有 Agent → 最近运行」点击任意一条记录，这里会加载对应审计视图。</div>
    </div>
    <div class="card replay" id="replay">
      <div class="replay-head">
        <h2>审计视图 · <code id="replay-trace"></code></h2>
        <div class="replay-actions">
          <button class="primary" id="play">播放</button>
          <button class="small" id="integrity-check">校验证据链</button>
          <span class="muted" id="integrity-result"></span>
          <a id="replay-report" target="_blank">打开完整报告 →</a>
        </div>
      </div>
      <div class="view-note" id="replay-summary">选择一条记录后加载审计视图。</div>
      <div id="crew-panel" hidden></div>
      <div class="view-tabs" id="replay-tabs">
        <button class="view-tab active" data-view="timeline">任务时间线</button>
        <button class="view-tab" data-view="resources">资源触碰</button>
        <button class="view-tab" data-view="permissions">权限与授权</button>
        <button class="view-tab" data-view="decisions">决策来源</button>
        <button class="view-tab" data-view="impact">持久变更</button>
      </div>
      <div class="view-pane" id="view-timeline">
        <p class="view-note">按执行顺序回答“智能体做了什么”。点击任意步骤查看原始参数与命中证据。</p>
        <div class="track" id="track"><div class="playhead" id="playhead"></div></div>
        <div id="replay-list"></div>
      </div>
      <div class="view-pane" id="view-resources" hidden>
        <p class="view-note">回答“碰了哪些资源”。红色为写入，黄色为外部执行，绿色为只读；点击“定位步骤”可回到时间线查看原始调用。</p>
        <div class="res-grid" id="resource-grid"></div>
      </div>
      <div class="view-pane" id="view-permissions" hidden>
        <p class="view-note">回答“谁给了什么权限、用在了哪”。客户端自报的人工确认不等同于可信审批。</p>
        <div id="permission-list"></div>
      </div>
      <div class="view-pane" id="view-decisions" hidden>
        <p class="view-note">回答“为什么做这个决策”：前置提示 → 检索上下文 → 工具调用 → 结果。</p>
        <div id="decision-list"></div>
      </div>
      <div class="view-pane" id="view-impact" hidden>
        <p class="view-note">回答“任务结束后还剩什么影响”。安轨不持有业务系统回滚能力，撤销需在业务侧执行。</p>
        <div id="impact-banner"></div>
        <div class="table-scroll" tabindex="0" role="region" aria-label="持久变更表格"><table>
          <thead><tr><th>时间</th><th>工具</th><th>目标</th><th>结果</th><th>状态</th></tr></thead>
          <tbody id="impact-list"><tr><td colspan="5" class="empty">加载中…</td></tr></tbody>
        </table></div>
      </div>
    </div>
    <div class="hint">数据与证据导出已移至「系统设置 → 数据管理」，证据包包含轨迹、报告、哈希链、策略与操作日志。</div>
  </div>

  <!-- 策略中心 -->
  <div class="panel" id="panel-policies">
    <div class="page-heading"><div class="eyebrow">POLICY / 02</div><h1>规则与边界</h1><p>为不同智能体设置：可以做什么、不能做什么、什么时候需要人工确认。</p></div>
    <div class="first-run" id="policy-mode-hint" hidden></div>
    <details class="card" id="policy-form">
      <summary><span><span class="summary-title" id="policy-form-title">新建策略</span><span class="summary-meta">按需展开，常用项已放在最前</span></span></summary>
      <div class="card-body">
        <div class="form">
          <label class="field">智能体 ID（* 表示默认策略）
            <input id="p-agent" placeholder="例如 cs-agent 或 *">
          </label>
          <label class="field">策略名称
            <input id="p-name" placeholder="例如 客服智能体生产策略">
          </label>
          <label class="field">运行模式
            <select id="p-mode">
              <option value="shadow">仅记录（推荐先用）</option>
              <option value="enforce">允许阻断</option>
            </select>
          </label>
          <label class="field">会话预算（美元，可留空）
            <input id="p-budget" type="number" step="0.000001" placeholder="0.00003">
          </label>
          <label class="field">允许使用的工具（逗号分隔，留空不限制）
            <input id="p-tools" placeholder="get_order, search_policy, send_message">
          </label>
          <label class="field">写操作金额阈值（可留空）
            <input id="p-threshold" type="number" step="1" placeholder="1000">
          </label>
          <label class="field">重复调用阈值
            <input id="p-loop" type="number" min="1" value="3">
          </label>
          <label class="field">写操作必须人工确认
            <select id="p-confirm">
              <option value="1">是</option>
              <option value="0">否</option>
            </select>
          </label>
          <label class="field">入库前脱敏
            <select id="p-redact">
              <option value="0">否</option>
              <option value="1">是</option>
            </select>
          </label>
          <label class="field full">启用规则（不勾选表示全部启用）
            <span class="checks" id="p-rules"></span>
          </label>
          <label class="field full">备注
            <input id="p-note" placeholder="策略用途、变更原因">
          </label>
        </div>
        <div class="actions">
          <button class="primary" id="policy-save">保存策略</button>
          <button id="policy-reset">清空表单</button>
          <button id="policy-simulate">试算最近 100 条</button>
          <span class="muted" id="policy-msg"></span>
        </div>
        <div id="policy-sim-result" class="hint" hidden></div>
      </div>
    </details>
    <details class="card wide" id="policy-list-card">
      <summary><span><span class="summary-title">策略列表</span><span class="summary-meta" id="policy-count">加载中…</span></span></summary>
      <div class="card-body">
        <div class="table-scroll" tabindex="0" role="region" aria-label="策略列表表格"><table>
          <thead><tr><th>智能体</th><th>名称</th><th>模式</th><th>预算</th><th>白名单</th><th>金额阈值</th><th>规则</th><th>操作</th></tr></thead>
          <tbody id="policies"><tr><td colspan="8" class="empty">加载中…</td></tr></tbody>
        </table></div>
      </div>
    </details>
    <details class="card wide" id="system-status-card" hidden>
      <summary><span><span class="summary-title">运行状态 · 管理员</span><span class="summary-meta">依赖健康、全局模式与累计计数</span></span></summary>
      <div class="card-body">
        <div class="hint">就绪检查会验证数据库可用性；出站依赖（裁判、通知）连续失败会触发熔断，熔断期间自动跳过而不是拖垮审计主链路。</div>
        <div class="mode-row">
          <label class="field">全局运行模式
            <select id="global-mode">
              <option value="shadow">影子模式（只记录，不阻断）</option>
              <option value="enforce">拦截模式（允许阻断）</option>
            </select>
          </label>
          <button id="global-mode-save" class="primary">应用到全局</button>
          <span class="mode-note" id="global-mode-msg" role="status"></span>
        </div>
        <div class="actions"><button id="system-status-refresh" class="primary">刷新运行状态</button><span class="muted" id="system-status-msg" role="status"></span></div>
        <div class="table-scroll" tabindex="0" role="region" aria-label="运行状态表格"><table>
          <thead><tr><th>名称</th><th>状态</th><th>说明</th></tr></thead>
          <tbody id="system-status"><tr><td colspan="3" class="empty">点击刷新加载</td></tr></tbody>
        </table></div>
      </div>
    </details>
  </div>

  <!-- 处置中心 -->
  <div class="panel" id="panel-dispositions">
    <div class="card" id="approval-detail" hidden>
      <div class="replay-head" style="margin-bottom:10px">
        <h2 style="border:0;padding:0;margin:0">审批详情 · <code id="apd-title"></code></h2>
        <div class="replay-actions">
          <span class="badge review" id="apd-risk"></span>
          <span class="apd-count" id="apd-countdown"></span>
        </div>
      </div>
      <div class="apd-grid">
        <div>
          <div class="drawer-label">变更 / 调用参数</div>
          <pre id="apd-diff" style="white-space:pre-wrap;overflow-wrap:anywhere"></pre>
        </div>
        <div>
          <div class="drawer-label">影响范围</div>
          <div class="apd-impact" id="apd-impact"></div>
        </div>
      </div>
      <label class="field" style="margin-top:14px">审批理由（高风险必填）
        <textarea id="apd-note" rows="3" maxlength="200" placeholder="说明为什么批准或驳回"></textarea>
      </label>
      <div class="actions">
        <button class="primary" id="apd-approve" type="button">批准</button>
        <button class="danger" id="apd-reject" type="button">驳回</button>
        <button id="apd-chain" type="button">查看决策链</button>
        <span class="muted" id="apd-msg"></span>
      </div>
    </div>
    <details class="card wide" id="approval-card">
      <summary><span><span class="summary-title">写操作审批</span><span class="summary-meta" id="approvals-count">加载中…</span></span></summary>
      <div class="card-body">
        <div class="hint" style="margin-top:0">
          写操作在“允许阻断”模式下需要一次性审批。批准后只对同一智能体、同一工具、同一参数生效，默认 15 分钟有效且只能使用一次。
        </div>
        <div class="table-scroll" tabindex="0" role="region" aria-label="写操作审批表格"><table>
          <thead><tr><th>时间</th><th>智能体</th><th>工具</th><th>状态</th><th>审批人</th><th>有效期</th><th>操作</th></tr></thead>
          <tbody id="approvals"><tr><td colspan="7" class="empty">加载中…</td></tr></tbody>
        </table></div>
      </div>
    </details>
    <div class="card" style="margin-top:18px">
      <h2>风险处置提案</h2>
      <div class="table-scroll" tabindex="0" role="region" aria-label="风险处置提案表格"><table>
        <thead><tr><th>时间</th><th>智能体</th><th>动作</th><th>原因</th><th>级别</th><th>状态</th><th>操作</th></tr></thead>
        <tbody id="dispositions"><tr><td colspan="7" class="empty">加载中…</td></tr></tbody>
      </table></div>
    </div>
    <div class="runtime">
      <details class="card" id="runtime-isolated-card">
        <summary><span><span class="summary-title">已隔离会话</span><span class="summary-meta" id="runtime-isolated-count">0</span></span></summary>
        <div class="card-body"><div id="runtime-isolated"><div class="empty">暂无隔离会话</div></div></div>
      </details>
      <details class="card" id="runtime-tools-card">
        <summary><span><span class="summary-title">已阻断工具</span><span class="summary-meta" id="runtime-tools-count">0</span></span></summary>
        <div class="card-body"><div id="runtime-tools"><div class="empty">暂无阻断工具</div></div></div>
      </details>
    </div>
    <details class="card" id="manual-disposition-card" style="margin-top:18px">
      <summary><span><span class="summary-title">人工处置提案</span><span class="summary-meta">自动提案没覆盖时，可在这里补一条</span></span></summary>
      <div class="card-body">
        <div class="form">
          <label class="field">智能体<input id="d-agent" placeholder="必须填写明确智能体"></label>
          <label class="field">动作
            <select id="d-action">
              <option value="require_approval">要求人工审批</option>
              <option value="isolate">隔离会话</option>
              <option value="block_tool">阻断工具</option>
              <option value="notify">通知值班人</option>
              <option value="ignore">忽略</option>
            </select>
          </label>
          <label class="field">目标工具（阻断用）<input id="d-target" placeholder="例如 refund"></label>
          <label class="field">Session（隔离用）<input id="d-session" placeholder="session id"></label>
          <label class="field">级别
            <select id="d-severity">
              <option value="low">low</option>
              <option value="medium" selected>medium</option>
              <option value="high">high</option>
              <option value="critical">critical</option>
            </select>
          </label>
          <label class="field full">原因<input id="d-reason" placeholder="为什么需要处置"></label>
        </div>
        <div class="actions">
          <button class="primary" id="d-create">提交提案</button>
          <span class="muted" id="d-msg"></span>
        </div>
      </div>
    </details>
  </div>

  <!-- 代码审计 -->
  <div class="panel" id="panel-codeaudit">
    <div class="page-heading"><div class="eyebrow">CODE SECURITY / 04</div><h1>代码检查</h1><p>检查项目代码中的密钥泄露、危险命令、注入等常见风险，并把结果交给技术人员修复。</p></div>
    <div class="card">
      <h2>检查项目代码</h2>
      <div class="hint" style="margin-top:0">
        支持 Python 及其他常见语言的规则检查；每条结论都会标出文件和行号，方便直接定位。
      </div>
      <div class="dropzone" id="code-dropzone" role="button" tabindex="0"
        aria-label="把项目压缩包或代码文件拖到这里，或点击选择文件">
        <div class="dz-icon" aria-hidden="true">⇪</div>
        <div class="dz-title">把项目压缩包或代码文件拖到这里</div>
        <div class="dz-hint">支持 .zip、整个文件夹、单个文件；也可以点击这里选择</div>
        <input type="file" id="code-file" multiple hidden>
      </div>
      <div class="dz-alt">
        <button class="small" id="code-folder-btn" type="button">选择整个文件夹</button>
        <input type="file" id="code-folder" webkitdirectory directory multiple hidden>
        <span class="muted">文件只用于本次扫描，扫描结束立即删除。</span>
      </div>
      <div class="form" style="margin-top:16px">
        <label class="field full">或填写服务器本机路径（可选）
          <input id="code-path" placeholder="例如 d:\\code\\your-project">
        </label>
      </div>
      <div class="actions">
        <button class="primary" id="code-scan">检查本机路径</button>
        <a id="code-latest-link" target="_blank" class="muted" style="align-self:center;font-size:14px">打开最近一次 HTML 报告 →</a>
        <span class="muted" id="code-msg"></span>
      </div>
    </div>
    <details class="card wide" id="code-result-card">
      <summary><span><span class="summary-title">检查结果</span><span class="summary-meta" id="code-summary">还没有检查结果</span></span></summary>
      <div class="card-body">
        <div class="table-scroll" tabindex="0" role="region" aria-label="代码检查结果表格"><table>
          <thead><tr><th>级别</th><th>问题</th><th>位置</th><th>建议</th></tr></thead>
          <tbody id="code-findings"><tr><td colspan="4" class="empty">还没有检查结果</td></tr></tbody>
        </table></div>
      </div>
    </details>
    <details class="card wide" id="extended-audit-card">
      <summary><span><span class="summary-title">扩展审计</span><span class="summary-meta">依赖、基础设施与红队检测</span></span></summary>
      <div class="card-body">
        <details class="fold">
          <summary>依赖与供应链</summary>
          <div class="fold-body">
            <div class="form">
              <label class="field full">项目路径<input id="sca-path" placeholder="例如 d:\\code\\your-project"></label>
            </div>
            <div class="actions">
              <button class="primary" id="sca-run">扫描依赖</button>
              <span class="muted" id="sca-msg"></span>
            </div>
            <div class="table-scroll" tabindex="0" role="region" aria-label="依赖扫描结果表格"><table>
              <thead><tr><th>级别</th><th>依赖</th><th>版本</th><th>漏洞</th><th>修复版本</th><th>位置</th></tr></thead>
              <tbody id="sca-findings"><tr><td colspan="6" class="empty">尚未扫描</td></tr></tbody>
            </table></div>
          </div>
        </details>
        <details class="fold">
          <summary>基础设施配置</summary>
          <div class="fold-body">
            <div class="form">
              <label class="field full">项目路径<input id="infra-path" placeholder="例如 d:\\code\\your-project"></label>
            </div>
            <div class="actions">
              <button class="primary" id="infra-run">扫描 Dockerfile / K8s</button>
              <span class="muted" id="infra-msg"></span>
            </div>
            <div class="table-scroll" tabindex="0" role="region" aria-label="基础设施扫描结果表格"><table>
              <thead><tr><th>级别</th><th>问题</th><th>位置</th><th>建议</th></tr></thead>
              <tbody id="infra-findings"><tr><td colspan="4" class="empty">尚未扫描</td></tr></tbody>
            </table></div>
          </div>
        </details>
      </div>
    </details>
  </div>

  <!-- 红队检测 -->
  <div class="panel" id="panel-redteam">
    <div class="page-heading"><div class="eyebrow">RED TEAM / 05</div><h1>红队检测</h1><p>用内置攻击提示集验证内容护栏，覆盖提示注入、越权索取、密钥外发与 PII 批量导出。</p></div>
    <div class="card">
      <h2>内置红队集</h2>
      <div class="hint" style="margin-top:0">检测针对内容护栏规则，不调用真实模型；每条提示都会给出命中规则与证据片段，未命中也会记录，方便补规则。</div>
      <div class="actions">
        <button class="primary" id="redteam-run">运行内置红队集</button>
        <span class="muted" id="redteam-msg" role="status"></span>
      </div>
      <div class="table-scroll" tabindex="0" role="region" aria-label="红队检测结果表格"><table>
        <thead><tr><th>结果</th><th>提示</th><th>命中规则</th></tr></thead>
        <tbody id="redteam-findings"><tr><td colspan="3" class="empty">尚未运行</td></tr></tbody>
      </table></div>
    </div>
  </div>

  <div class="panel" id="panel-settings">
    <div class="page-heading"><div class="eyebrow">SYSTEM / 04</div><h1>系统设置</h1><p>模型连接、检索与裁决参数、裁判提示词，以及本地数据管理。改动保存后对下一次审计生效。</p></div>
    <div class="sys-switch" role="tablist" aria-label="系统设置分区">
      <button class="sys-switch-btn active" type="button" data-sys="config" role="tab">系统配置</button>
      <button class="sys-switch-btn" type="button" data-sys="data" role="tab">数据管理</button>
    </div>

    <div class="sys-pane" id="sys-pane-config">
      <div class="sys-subtabs" role="tablist" aria-label="系统配置分类">
        <button class="sys-subtab active" type="button" data-sys-sub="models" role="tab">模型连接</button>
        <button class="sys-subtab" type="button" data-sys-sub="retrieval" role="tab">检索配置</button>
        <button class="sys-subtab" type="button" data-sys-sub="params" role="tab">分析参数</button>
        <button class="sys-subtab" type="button" data-sys-sub="prompt" role="tab">裁判提示词</button>
      </div>

      <div class="sys-subpane active" id="sys-sub-models">
        <div class="hint" id="model-readonly-hint" hidden style="margin-top:0">仅管理员可修改模型连接；当前生效裁判可在审计报告中查看。</div>
        <details class="card wide" id="model-settings" hidden>
          <summary><span><span class="summary-title">模型连接 · 管理员</span><span class="summary-meta">连接、密钥与独立裁判</span></span></summary>
          <div class="card-body">
            <div id="ms-source" class="hint" style="margin-top:0"></div>
            <fieldset id="ms-editor" hidden>
              <legend>连接配置</legend>
              <div class="form">
                <label class="field full">模型提供商
                  <select id="ms-provider"><option value="openai">OpenAI</option><option value="deepseek">DeepSeek</option><option value="dashscope">DashScope</option><option value="custom">自定义兼容服务</option></select>
                </label>
                <label class="field full">API 密钥（留空保留）
                  <input id="ms-key" type="password" autocomplete="new-password" spellcheck="false" placeholder="输入 API Key">
                  <span class="muted">加密保存在本机，不回显。</span>
                </label>
                <label class="field">模型名称
                  <input id="ms-model" placeholder="例如 deepseek-chat">
                </label>
                <label class="field">API 基础 URL
                  <input id="ms-url" type="url" placeholder="https://api.deepseek.com">
                  <span class="muted">仅支持公网 HTTPS 443。</span>
                </label>
              </div>
              <details class="fold" id="ms-advanced">
                <summary>高级选项</summary>
                <div class="fold-body">
                  <div class="form">
                    <label class="field">稳定英文别名<input id="ms-id" autocomplete="off" placeholder="例如 primary-model"></label>
                    <label class="field">显示名<input id="ms-name"></label>
                  </div>
                  <div class="checks">
                    <label><input id="ms-gateway" type="checkbox">网关用途</label>
                    <label><input id="ms-purpose-judge" type="checkbox">裁判用途</label>
                    <label><input id="ms-stream" type="checkbox">支持流式</label>
                    <label><input id="ms-json" type="checkbox">支持 JSON 对象</label>
                    <label><input id="ms-clear" type="checkbox">清除已有 Key</label>
                  </div>
                </div>
              </details>
              <div class="actions">
                <button id="ms-test">测试连接</button>
                <button id="ms-save" class="primary">保存并生效</button>
                <button id="ms-add">新增连接</button>
                <button id="ms-reload">重新加载</button>
                <button id="ms-close">取消</button>
              </div>
              <div id="ms-msg" class="hint" role="status"></div>
              <div id="ms-dirty" class="hint" role="status"></div>
            </fieldset>
            <div class="table-scroll" tabindex="0" role="region" aria-label="模型连接表格"><table><thead><tr><th>别名 / 显示名</th><th>模型 / 供应商</th><th>Key 状态</th><th>操作</th></tr></thead><tbody id="ms-list"></tbody></table></div>
            <details class="fold" id="ms-global">
              <summary>默认用途</summary>
              <div class="fold-body">
                <div class="form">
                  <label class="field">默认网关<select id="ms-default"></select></label>
                  <label class="field">独立裁判连接<select id="ms-judge"></select></label>
                  <label class="field">独立裁判开关<input id="ms-enabled" type="checkbox"></label>
                </div>
              </div>
            </details>
          </div>
        </details>
      </div>

      <div class="sys-subpane" id="sys-sub-retrieval">
        <div class="card">
          <h2>审计知识库</h2>
          <div class="hint">关键词检索（SQLite FTS5 + BM25），不依赖向量模型。文档按二级标题切节，再按 520 字切片、重叠 80 字；裁判只引用命中片段，引用编号由后端映射。</div>
          <div class="params-grid">
            <div class="param-item"><div class="k">文档</div><div class="v" id="kb-documents">—</div></div>
            <div class="param-item"><div class="k">切片</div><div class="v" id="kb-chunks">—</div></div>
            <div class="param-item"><div class="k">分词器</div><div class="v" id="kb-tokenizer">—</div></div>
            <div class="param-item"><div class="k">召回条数</div><div class="v" id="kb-limit">—</div></div>
          </div>
          <div class="actions"><button id="kb-refresh" class="primary">刷新</button><button id="kb-rebuild">重建索引</button><span class="muted" id="kb-msg" role="status"></span></div>
        </div>
      </div>

      <div class="sys-subpane" id="sys-sub-params">
        <details class="card" id="audit-params-card">
          <summary><span><span class="summary-title">分析参数 · 管理员</span><span class="summary-meta">超时、重试、输出与输入上限</span></span></summary>
          <div class="card-body">
            <div class="hint">保存后对下一次审计生效；温度固定为 0，同一轨迹必须得到同一结论。推理模型需要更大的输出额度，否则会被 reasoning 占满而返回空内容。</div>
            <div class="form">
              <label class="field">单次模型请求超时（秒）<input id="ap-timeout" type="number" min="5" max="180" step="1"></label>
              <label class="field">失败重试次数（0–5）<input id="ap-retries" type="number" min="0" max="5" step="1"></label>
              <label class="field">单次输出上限（token）<input id="ap-tokens" type="number" min="400" max="8000" step="100"></label>
              <label class="field">轨迹输入上限（字符）<input id="ap-chars" type="number" min="2000" max="60000" step="1000"></label>
              <label class="field">知识库召回条数（1–8）<input id="ap-kb" type="number" min="1" max="8" step="1"></label>
              <label class="field">温度（固定 0）<input value="0" disabled></label>
            </div>
            <div class="actions"><button id="ap-save" class="primary">保存并生效</button><button id="ap-reload">重新加载</button><span class="muted" id="ap-msg" role="status"></span></div>
          </div>
        </details>
      </div>

      <div class="sys-subpane" id="sys-sub-prompt">
        <div class="card" id="judge-prompt-fold">
          <h2>裁判提示词（只读）</h2>
          <div class="hint" id="judge-prompt-meta" style="margin-top:0">加载中…</div>
          <pre id="judge-prompt-text" style="max-height:420px">加载中…</pre>
          <div class="hint" id="judge-prompt-guards"></div>
        </div>
      </div>
    </div>

    <div class="sys-pane" id="sys-pane-data" hidden>
      <div class="card">
        <h2>数据库健康与统计</h2>
        <div class="hint">统计来自当前 SQLite 实例；轨迹是原始证据，清理前请先导出证据包。</div>
        <div class="params-grid">
          <div class="param-item"><div class="k">状态</div><div class="v" id="dm-db">—</div></div>
          <div class="param-item"><div class="k">数据库体积</div><div class="v" id="dm-size">—</div></div>
          <div class="param-item"><div class="k">轨迹</div><div class="v" id="dm-traces">—</div></div>
          <div class="param-item"><div class="k">智能体</div><div class="v" id="dm-agents">—</div></div>
          <div class="param-item"><div class="k">待处理审批</div><div class="v" id="dm-approvals">—</div></div>
          <div class="param-item"><div class="k">策略</div><div class="v" id="dm-policies">—</div></div>
          <div class="param-item"><div class="k">审计事件</div><div class="v" id="dm-events">—</div></div>
        </div>
        <div class="actions"><button id="dm-refresh" class="primary">刷新统计</button><span class="muted" id="dm-msg" role="status"></span></div>
      </div>
      <details class="card wide" id="data-governance-card">
        <summary><span><span class="summary-title">数据与证据</span><span class="summary-meta">保留策略、清理与证据包</span></span></summary>
        <div class="card-body">
          <div class="hint">按保留策略预览或清理历史数据；导出包含轨迹、报告、哈希链、策略和操作日志的 zip 证据包。</div>
          <div class="actions">
            <button id="retention-preview">查看保留预演</button>
            <button id="evidence-export" class="primary">导出证据包</button>
            <span class="muted" id="data-msg"></span>
          </div>
          <div id="retention-result" class="hint"></div>
          <a id="evidence-link" hidden target="_blank">下载证据包 →</a>
        </div>
      </details>
    </div>
  </div>

  <div class="panel" id="panel-agents">
    <details class="hint gateway-hint" style="margin:0 0 14px">
      <summary><span>技术说明：接入地址、Agent Key 与拦截边界</span></summary>
      <div class="gateway-body">
        被审计智能体的 <code>base_url</code> 指向 <code id="gateway-url">http://127.0.0.1:8000/v1</code>；
        需配置独立 Agent Key；工具执行前检查须由 SDK 或 MCP 显式启用。MCP 默认仅采集，服务端规则决定检查结果。
      </div>
    </details>
    <details class="card wide" id="agent-management-card">
      <summary><span><span class="summary-title">智能体管理</span><span class="summary-meta">注册、启停与查看审计状态</span></span></summary>
      <div class="card-body">
        <div class="hint">已认证仅表示凭据校验成功；已认领记录数包含调用前预留，不代表完整接收。只有生成报告才表示该记录已有审计结果；accepted 不等于审计完成。管理操作仅限管理员。</div>
        <div class="form" id="agent-register-form">
          <label class="field">智能体 ID<input id="agent-id" placeholder="例如 cs-agent"></label>
          <label class="field">显示名称<input id="agent-name" placeholder="例如 客服智能体"></label>
        </div>
        <div class="actions"><button id="agent-register" class="primary">注册智能体</button><button id="agent-refresh">刷新状态</button><span id="agent-msg" class="muted" role="status"></span></div>
        <div class="table-scroll" tabindex="0" role="region" aria-label="智能体管理表格"><table><thead><tr><th>智能体 / 名称</th><th>启用</th><th>最近认证</th><th>已认领记录</th><th>已生成报告</th><th>操作</th></tr></thead><tbody id="agents"></tbody></table></div>
      </div>
    </details>
    <details class="card wide" id="version-card">
      <summary><span><span class="summary-title">版本对比</span><span class="summary-meta">判断新版本是否引入更多风险</span></span></summary>
      <div class="card-body">
        <div class="hint">按智能体版本聚合记录量、风险率、成本与平均命中，用于判断新版本是否退化。</div>
        <div class="form">
          <label class="field">智能体 ID<input id="v-agent" placeholder="例如 cs-agent"></label>
        </div>
        <div class="actions"><button id="v-load" class="primary">加载版本对比</button><span class="muted" id="v-msg" role="status"></span></div>
        <div class="table-scroll" tabindex="0" role="region" aria-label="版本对比表格"><table>
          <thead><tr><th>版本</th><th>记录</th><th>风险记录</th><th>风险率</th><th>成本</th><th>平均命中</th><th>最近</th></tr></thead>
          <tbody id="agent-versions"><tr><td colspan="7" class="empty">输入智能体 ID 后加载</td></tr></tbody>
        </table></div>
      </div>
    </details>
    <div class="card wide" id="agent-detail" hidden>
      <h2>凭据与接入配置 · <code id="agent-selected"></code></h2>
      <div class="hint">轮换：签发新 Key → 使用新 Key 验证认证与报告 → 撤销旧 Key。不要把 Key 写入模板、代码或聊天。</div>
      <div id="agent-key-admin">
        <div class="checks" id="agent-scopes">
          <label><input type="checkbox" value="models:read">models:read</label>
          <label><input type="checkbox" value="gateway:invoke">gateway:invoke</label>
          <label><input type="checkbox" value="tools:check">tools:check</label>
          <label><input type="checkbox" value="traces:write" checked>traces:write</label>
        </div>
        <label class="field">有效期（本地时间，留空不过期）<input id="agent-expiry" type="datetime-local"></label>
        <div class="actions"><button id="agent-issue" class="primary">签发 Key</button></div>
        <div id="agent-secret-box" hidden>
          <div class="hint">原始 Key 仅展示一次；离开页面、切换 Agent 或退出时清除。复制后请在本机设置环境变量。</div>
          <label class="field">一次性 Key<input id="agent-secret" readonly autocomplete="off" spellcheck="false"></label>
          <div class="actions"><button id="agent-secret-copy">复制 Key</button><button id="agent-secret-clear">清除 Key</button></div>
        </div>
        <div class="table-scroll" tabindex="0" role="region" aria-label="可横向滚动的数据表格"><table><thead><tr><th>前缀</th><th>权限</th><th>有效期</th><th>状态</th><th>操作</th></tr></thead><tbody id="agent-keys"></tbody></table></div>
      </div>
      <div class="hint">PowerShell 环境变量模板（请在本机替换占位符）：</div>
      <pre>$env:SAFEORBIT_AGENT_KEY = '&lt;在本机填入一次性 Agent Key&gt;'</pre>
      <label class="field">接入方式<select id="agent-framework"><option value="generic">通用 SDK</option><option value="openai">OpenAI SDK</option><option value="langchain">LangChain</option><option value="langgraph">LangGraph</option><option value="llamaindex">LlamaIndex</option><option value="autogen">AutoGen</option><option value="crewai">CrewAI</option><option value="otel">OTLP HTTP JSON / protobuf</option><option value="mcp">MCP（stdio）</option><option value="mcp-http">MCP（HTTP）</option></select></label>
      <label class="field" id="agent-model-field" hidden>OpenAI SDK 请求模型别名
        <select id="agent-model" disabled><option value="">请先加载模型目录</option></select>
        <span>仅列出网关用途且密钥已配置的别名；模拟模式仅使用 mock-model，不调用真实上游。</span>
      </label>
      <pre id="agent-snippet" style="white-space:pre-wrap;overflow-wrap:anywhere"></pre>
      <div class="actions"><button id="agent-snippet-copy">复制模板（不含 Key）</button></div>
      <div class="hint">测试：在本机设置环境变量后运行模板，然后刷新状态，分别检查最近认证、轨迹认领及报告数。策略与规则请在“策略中心”查看。</div>
    </div>
  </div>

  <footer>SafeOrbit 0.5 · 独立审计层 · 数据保存在本地 SQLite</footer>
    </div><!-- /l3-body -->
  </main>
</div><!-- /app -->

<aside class="drawer" id="drawer" hidden aria-label="详情面板">
  <div class="drawer-head"><span id="drawer-title">详情</span>
    <button class="small" type="button" id="drawer-close">关闭 Esc</button></div>
  <div class="drawer-body" id="drawer-body"></div>
</aside>
<div class="drawer-scrim" id="drawer-scrim" hidden></div>

<div class="palette-scrim" id="palette-scrim" hidden></div>
<div class="palette" id="palette" hidden role="dialog" aria-label="命令面板">
  <input id="palette-input" type="text" placeholder="搜索运行 ID、资源名、审批项，或跳转模块与操作…" autocomplete="off">
  <div class="palette-list" id="palette-list"></div>
</div>

<div id="landing">
  <canvas id="landing-canvas" aria-hidden="true"></canvas>
  <div class="landing-shell">
    <nav class="landing-nav">
      <div class="landing-logo">Safe<span>Orbit</span><small>安轨</small></div>
      <div class="landing-nav-right">
        <span class="landing-status" id="landing-status"><span class="dot"></span><span id="landing-status-text">正在检测服务状态</span></span>
        <button class="primary" id="landing-enter">进入控制台</button>
      </div>
    </nav>

    <section class="landing-hero">
      <div class="landing-copy landing-rise">
        <div class="eyebrow">给 AI 智能体装一个“行车记录仪”</div>
        <h1>它做了什么，<br><em>看得见、拦得住、说得清</em></h1>
        <p>安轨是一个记录和看管 AI 智能体的小系统。智能体每次调用模型、查资料、动数据，都会被自动记下来；遇到危险操作会先停下来等人确认；事后还能给出可以核对的凭证。不用改业务代码，也不用懂大模型细节。</p>
        <div class="landing-cta">
          <button class="primary" id="landing-enter-cta">进入控制台</button>
          <button class="ghost" id="landing-capabilities-jump">看看它能做什么</button>
        </div>
        <ul class="landing-facts">
          <li><b>看得见</b><span>每次调用都有记录</span></li>
          <li><b>拦得住</b><span>危险操作先审批</span></li>
          <li><b>说得清</b><span>报告可核对、防篡改</span></li>
        </ul>
      </div>
      <div class="landing-visual landing-rise d1" aria-hidden="true">
        <div class="landing-glow"></div>
        <div class="orbit">
          <div class="orbit-ring r3"></div>
          <div class="orbit-ring r2"></div>
          <div class="orbit-ring r1"></div>
          <div class="orbit-core"><svg viewBox="0 0 24 24" width="32" height="32" aria-hidden="true"><defs><linearGradient id="lgCoreL" x1="0.15" y1="0" x2="0.7" y2="1"><stop offset="0" stop-color="#D6FFF4"/><stop offset=".45" stop-color="#8DEDD8"/><stop offset="1" stop-color="#3FAF99"/></linearGradient><linearGradient id="lgCoreR" x1="0.3" y1="0" x2="0.9" y2="1"><stop offset="0" stop-color="#6FE3CB"/><stop offset=".55" stop-color="#3CB49E"/><stop offset="1" stop-color="#1D6358"/></linearGradient><mask id="lgCoreCut"><rect width="24" height="24" fill="#ffffff"/><path d="M8.6 11.4 10.8 13.7 15.5 8.9" fill="none" stroke="#000000" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></mask></defs><g mask="url(#lgCoreCut)"><path d="M12 2 4.5 4.9V11.6C4.5 16 7.7 19.3 12 20.9Z" fill="url(#lgCoreL)"/><path d="M12 2 19.5 4.9V11.6C19.5 16 16.3 19.3 12 20.9Z" fill="url(#lgCoreR)"/><path d="M12 2 19.5 4.9V7L12 4.4 4.5 7V4.9Z" fill="#EFFFFB" fill-opacity=".24"/><path d="M12 2V5.4" stroke="#08211D" stroke-opacity=".45" stroke-width=".8" stroke-linecap="round"/><path d="M12 16.8V20.9" stroke="#08211D" stroke-opacity=".45" stroke-width=".8" stroke-linecap="round"/></g><path d="M12 2 19.5 4.9V11.6C19.5 16 16.3 19.3 12 20.9 7.7 19.3 4.5 16 4.5 11.6V4.9Z" fill="none" stroke="#D6FFF4" stroke-opacity=".55" stroke-width=".8" stroke-linejoin="round"/></svg></div>
        </div>
      </div>
    </section>

    <section class="landing-section" id="landing-capabilities">
      <div class="landing-section-head">
        <div class="eyebrow">CAPABILITIES / 它能帮你做什么</div>
        <h2>四件事，覆盖“记录 → 提醒 → 拦截 → 举证”</h2>
        <p>不用一次全用上。先打开记录，看到问题再加提醒和审批即可。</p>
      </div>
      <div class="landing-grid">
        <article class="landing-card">
          <div class="landing-card-num">01</div>
          <h3>看它做了什么</h3>
          <p>智能体每次调模型、查资料、用工具，都会留下一条记录。点开就能看到当时说了什么、用了什么参数、拿到了什么结果。</p>
          <div class="landing-chips"><span>自动记录</span><span>原始内容</span></div>
        </article>
        <article class="landing-card">
          <div class="landing-card-num">02</div>
          <h3>有风险会提醒</h3>
          <p>敏感信息、越权操作、异常花费、答非所问，命中就标红并说明原因。你可以在“实时提醒”里第一时间看到。</p>
          <div class="landing-chips"><span>风险提醒</span><span>定位到具体步骤</span></div>
        </article>
        <article class="landing-card">
          <div class="landing-card-num">03</div>
          <h3>危险动作先停下来</h3>
          <p>退款、删除、转账这类写操作，会先生成一张审批单。人看过理由点“同意”之后才放行，没同意就不会执行。</p>
          <div class="landing-chips"><span>人工审批</span><span>一次性放行</span></div>
        </article>
        <article class="landing-card">
          <div class="landing-card-num">04</div>
          <h3>事后能拿出证据</h3>
          <p>每份报告都带防篡改指纹，可以一键核对、整包导出。复盘或对外说明时，能证明“当时到底发生了什么”。</p>
          <div class="landing-chips"><span>防篡改</span><span>证据包导出</span></div>
        </article>
      </div>
    </section>

    <section class="landing-section">
      <div class="landing-section-head">
        <div class="eyebrow">WHY SAFEORBIT / 评审速览</div>
        <h2>四个维度，逐条对得上</h2>
        <p>下面每一条都能在系统里当场看到，不靠形容词。</p>
      </div>
      <div class="review-grid">
        <article class="landing-card">
          <div class="landing-card-num">30% · 场景价值</div>
          <h3>针对真实事故，不是假想需求</h3>
          <p>客服智能体越权退款、模型编造承诺、检索文档被投毒——这三类事故在演示数据里都能复现，并给出对应判定与证据。</p>
          <div class="landing-chips"><span>越权退款</span><span>幻觉</span><span>RAG 注入</span></div>
        </article>
        <article class="landing-card">
          <div class="landing-card-num">30% · AI 应用深度</div>
          <h3>规则打底，模型只做语义复核</h3>
          <p>确定性规则先给硬结论；语义裁判用温度 0、强制 JSON、定位校验和失败降级，既用上模型能力，又不让模型决定结论。提示词可在控制台只读查看。</p>
          <div class="landing-chips"><span>提示词可见</span><span>定位校验</span><span>失败降级</span></div>
        </article>
        <article class="landing-card">
          <div class="landing-card-num">25% · 完成度</div>
          <h3>能跑、能测、能部署</h3>
          <p>384 项回归测试、Docker 一键启动、演示注入命令、CI 流水线；从采集到审批、处置、证据导出是闭环，不是概念验证。</p>
          <div class="landing-chips"><span>384 项测试</span><span>Docker</span><span>一键演示</span></div>
        </article>
        <article class="landing-card">
          <div class="landing-card-num">15% · 推广潜力</div>
          <h3>十分钟接入，不改业务代码</h3>
          <p>网关、SDK、OTLP、MCP 四种接入方式按改造成本排序；单机可跑、离线可用，换行业只需换规则与策略，不用重写系统。</p>
          <div class="landing-chips"><span>零改码接入</span><span>离线可用</span><span>换行业换规则</span></div>
        </article>
      </div>
    </section>

    <section class="landing-section">
      <div class="landing-section-head">
        <div class="eyebrow">DEMO / 三分钟演示</div>
        <h2>现场可以这样走一遍</h2>
        <p>三条命令、三次点击，把“记录 → 判定 → 拦截”演完。</p>
      </div>
      <ol class="landing-flow">
        <li><strong>注入演示</strong><span>运行 <code>py -m safeorbit demo</code>，三条真实事故轨迹进入审计。</span></li>
        <li><strong>看结论</strong><span>点开“越权退款”那条，翻五个视图：时间线、资源、权限、决策、变更。</span></li>
        <li><strong>拦一次</strong><span>到“风险处置”给写操作填理由，批准后再看它变成一次性审批票。</span></li>
      </ol>
      <details class="landing-more">
        <summary>给工程师：技术细节与名词解释</summary>
        <div class="landing-more-body">
          <p>接入方式：OpenAI 兼容网关、OTLP（JSON / protobuf）、Python SDK、MCP stdio 与 HTTP、LangChain / LangGraph / LlamaIndex / AutoGen / CrewAI 回调。</p>
          <p>审计顺序：采集 → 归一化 → 确定性规则 → 语义裁判 → 判定 → 处置。规则永远先跑，模型复核只做补充，模型失败不影响审计结论。</p>
          <p>名词对照：轨迹 = 一次完整记录；规则 = 固定检查项；裁判 = 让模型复核语义问题；策略 = 允许 / 禁止清单；处置 = 隔离会话或阻断工具；证据链 = 报告之间的防篡改指纹。</p>
        </div>
      </details>
    </section>

    <div class="landing-foot">
      <span>数据存在你自己的服务器上 · 不改变业务代码 · 离线也能运行</span>
      <span>SafeOrbit 0.5</span>
    </div>
  </div>
</div>

<div id="login">
  <div class="login-card">
    <button class="login-back" id="login-back" type="button">← 返回介绍页</button>
    <div class="login-mark" aria-hidden="true"><svg viewBox="0 0 24 24" width="20" height="20"><defs><linearGradient id="lgLoginL" x1="0.15" y1="0" x2="0.7" y2="1"><stop offset="0" stop-color="#D6FFF4"/><stop offset=".45" stop-color="#8DEDD8"/><stop offset="1" stop-color="#3FAF99"/></linearGradient><linearGradient id="lgLoginR" x1="0.3" y1="0" x2="0.9" y2="1"><stop offset="0" stop-color="#6FE3CB"/><stop offset=".55" stop-color="#3CB49E"/><stop offset="1" stop-color="#1D6358"/></linearGradient><mask id="lgLoginCut"><rect width="24" height="24" fill="#ffffff"/><path d="M8.6 11.4 10.8 13.7 15.5 8.9" fill="none" stroke="#000000" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></mask></defs><g mask="url(#lgLoginCut)"><path d="M12 2 4.5 4.9V11.6C4.5 16 7.7 19.3 12 20.9Z" fill="url(#lgLoginL)"/><path d="M12 2 19.5 4.9V11.6C19.5 16 16.3 19.3 12 20.9Z" fill="url(#lgLoginR)"/><path d="M12 2 19.5 4.9V7L12 4.4 4.5 7V4.9Z" fill="#EFFFFB" fill-opacity=".24"/><path d="M12 2V5.4" stroke="#08211D" stroke-opacity=".45" stroke-width=".8" stroke-linecap="round"/><path d="M12 16.8V20.9" stroke="#08211D" stroke-opacity=".45" stroke-width=".8" stroke-linecap="round"/></g><path d="M12 2 19.5 4.9V11.6C19.5 16 16.3 19.3 12 20.9 7.7 19.3 4.5 16 4.5 11.6V4.9Z" fill="none" stroke="#D6FFF4" stroke-opacity=".55" stroke-width=".8" stroke-linejoin="round"/></svg></div>
    <div class="eyebrow">安轨 · 审计与治理台</div>
    <h1>登录 Safe<span style="color:var(--blue)">Orbit</span></h1>
    <div class="login-subtitle">进入审计控制台，查看智能体记录、处理风险与配置边界。</div>
    <label for="login-user">用户名</label>
    <input id="login-user" value="admin" autocomplete="username">
    <label for="login-pass">密码</label>
    <input id="login-pass" type="password" autocomplete="current-password">
    <button class="primary" id="login-button">登录</button>
    <div class="login-msg" id="login-msg" role="status"></div>
    <div class="muted">首次使用请在服务器本机执行 <code>py -m safeorbit user init</code>；忘记密码或旧默认账号请执行 <code>py -m safeorbit user reset-password</code>。</div>
  </div>
</div>

<script>
const LABELS = {pass: "通过", warn: "有风险", fail: "不通过", review: "待复核"};
const ROLE_RANK = {viewer: 1, operator: 2, auditor: 3, admin: 4};
const alerts = [];
let currentUser = null;
let rulesCache = [];
let editingPolicyId = null;
let replayData = null;
let playTimer = null;

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
function money(value) {
  const n = Number(value || 0);
  return "$" + n.toFixed(n !== 0 && Math.abs(n) < 0.01 ? 6 : 4);
}
function timeText(value) {
  if (!value) return "";
  const text = String(value);
  return text.length >= 19 ? text.slice(11, 19) : text;
}
function secondsText(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const total = Math.max(0, Math.round(Number(value)));
  if (total < 60) return total + " 秒";
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return minutes + " 分 " + (total % 60) + " 秒";
  return Math.floor(minutes / 60) + " 时 " + (minutes % 60) + " 分";
}
function percentText(value) {
  return (Number(value || 0) * 100).toFixed(1) + "%";
}
function can(role) {
  return currentUser && ROLE_RANK[currentUser.role] >= ROLE_RANK[role];
}
function resetSessionView() {
  currentUser = null;
  resetModelSettings();
  clearAgentSecret();
  selectedAgent = null;
  document.getElementById("agent-detail").hidden = true;
  document.getElementById("agent-keys").textContent = "";
  summaryToggleExport();
}
function setConsoleInert(value) {
  const shell = document.getElementById("app");
  if (shell) shell.inert = value;
}
function showLanding() {
  resetSessionView();
  document.getElementById("login").classList.remove("show");
  document.getElementById("login-pass").value = "";
  document.getElementById("login-msg").textContent = "";
  document.getElementById("landing").hidden = false;
  setConsoleInert(true);
  loadLandingHealth();
  startLandingMotion();
  if (landingReveal) landingReveal();
}
function showLogin() {
  resetSessionView();
  stopLandingMotion();
  document.getElementById("login-msg").textContent = "";
  document.getElementById("landing").hidden = true;
  setConsoleInert(true);
  document.getElementById("login").classList.add("show");
}
function hideLanding() {
  stopLandingMotion();
  document.getElementById("landing").hidden = true;
  setConsoleInert(false);
}
function hideLogin() {
  document.getElementById("login").classList.remove("show");
}
async function loadLandingHealth() {
  const box = document.getElementById("landing-status");
  const text = document.getElementById("landing-status-text");
  try {
    const response = await fetch("/health", {credentials: "same-origin"});
    if (!response.ok) throw new Error();
    const data = await response.json();
    box.classList.remove("offline");
    text.textContent = "服务在线 · " + (data.mode === "enforce" ? "拦截模式" : "影子模式");
  } catch (error) {
    box.classList.add("offline");
    text.textContent = "服务未就绪";
  }
}

// ------------------------- 介绍页视觉 -------------------------
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const starfield = (() => {
  const canvas = document.getElementById("landing-canvas");
  let ctx = null, frame = null, width = 0, height = 0, points = [];
  const pointer = {x: -9999, y: -9999};
  function resize() {
    if (!ctx) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    width = canvas.clientWidth || window.innerWidth;
    height = canvas.clientHeight || window.innerHeight;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const count = Math.max(26, Math.min(88, Math.round((width * height) / 24000)));
    points = [];
    for (let i = 0; i < count; i++) {
      points.push({
        x: Math.random() * width, y: Math.random() * height,
        vx: (Math.random() - .5) * .22, vy: (Math.random() - .5) * .22,
        r: Math.random() * 1.3 + .5,
      });
    }
  }
  function draw(move) {
    if (!ctx) return;
    ctx.clearRect(0, 0, width, height);
    for (let i = 0; i < points.length; i++) {
      const a = points[i];
      for (let j = i + 1; j < points.length; j++) {
        const b = points[j];
        const dx = a.x - b.x, dy = a.y - b.y;
        const d2 = dx * dx + dy * dy;
        if (d2 < 16000) {
          ctx.strokeStyle = "rgba(101,214,193," + (0.16 * (1 - d2 / 16000)).toFixed(3) + ")";
          ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        }
      }
      const mx = a.x - pointer.x, my = a.y - pointer.y;
      const md2 = mx * mx + my * my;
      if (md2 < 30000) {
        ctx.strokeStyle = "rgba(180,165,232," + (0.34 * (1 - md2 / 30000)).toFixed(3) + ")";
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(pointer.x, pointer.y); ctx.stroke();
      }
      ctx.fillStyle = "rgba(154,226,210,.8)";
      ctx.beginPath(); ctx.arc(a.x, a.y, a.r, 0, Math.PI * 2); ctx.fill();
      if (move) {
        a.x += a.vx; a.y += a.vy;
        if (a.x < -24) a.x = width + 24; else if (a.x > width + 24) a.x = -24;
        if (a.y < -24) a.y = height + 24; else if (a.y > height + 24) a.y = -24;
      }
    }
  }
  function loop() {
    draw(true);
    frame = window.requestAnimationFrame(loop);
  }
  function ensureContext() {
    if (ctx || !canvas) return !!ctx;
    ctx = canvas.getContext("2d");
    if (ctx) resize();
    return !!ctx;
  }
  return {
    start() {
      if (!ensureContext()) return;
      // 静态单次绘制：不跑逐帧动画，避免全屏 canvas 持续重绘导致黑屏
      draw(false);
    },
    stop() {
      if (frame) window.cancelAnimationFrame(frame);
      frame = null;
    },
    resize() { resize(); draw(false); },
  };
})();

let countersDone = false;
function animateLandingCounters() {
  if (countersDone) return;
  countersDone = true;
  document.querySelectorAll(".landing-facts b[data-count]").forEach(node => {
    const target = Number(node.dataset.count || 0);
    const suffix = node.dataset.suffix || "";
    if (reduceMotion.matches || !target) { node.textContent = target + suffix; return; }
    const started = window.performance.now();
    function tick(now) {
      const progress = Math.min(1, (now - started) / 900);
      const eased = 1 - Math.pow(1 - progress, 3);
      node.textContent = Math.round(target * eased) + suffix;
      if (progress < 1) window.requestAnimationFrame(tick);
    }
    window.requestAnimationFrame(tick);
  });
}

let landingReveal = null;
function setupLandingReveal() {
  const items = Array.prototype.slice.call(
    document.querySelectorAll(".landing-section-head, .landing-card, .landing-flow li, .landing-foot")
  );
  items.forEach((item, index) => {
    item.classList.add("landing-reveal");
    item.style.transitionDelay = ((index % 3) * 90) + "ms";
  });
  let queued = false;
  function reveal() {
    queued = false;
    const root = document.getElementById("landing");
    const rootRect = root.getBoundingClientRect();
    items.forEach(item => {
      if (item.classList.contains("in")) return;
      const rect = item.getBoundingClientRect();
      if (rect.top < rootRect.bottom - 60 && rect.bottom > rootRect.top) item.classList.add("in");
    });
  }
  function schedule() {
    if (queued) return;
    queued = true;
    window.requestAnimationFrame(reveal);
  }
  reveal();
  landingReveal = reveal;
  document.getElementById("landing").addEventListener("scroll", schedule, {passive: true});
  // 兜底：即使滚动事件异常，也不让内容永久隐藏
  window.setTimeout(() => { items.forEach(item => item.classList.add("in")); }, 2600);
}

function startLandingMotion() {
  try {
    starfield.start();
    animateLandingCounters();
  } catch (error) {}
}
function stopLandingMotion() {
  try { starfield.stop(); } catch (error) {}
}

const landingEl = document.getElementById("landing");
window.addEventListener("resize", () => { try { starfield.resize(); } catch (error) {} });

async function api(path, options) {
  const config = Object.assign({credentials: "same-origin", cache: "no-store"}, options || {});
  config.headers = new Headers(config.headers || {});
  if (!["GET", "HEAD", "OPTIONS"].includes((config.method || "GET").toUpperCase())) {
    config.headers.set("X-SafeOrbit-CSRF", "1");
  }
  const response = await fetch(path, config);
  if (response.status === 401) {
    showLogin();
    throw new Error("未登录");
  }
  let data = null;
  try { data = await response.json(); } catch (error) { data = null; }
  if (!response.ok) {
    const detail = (data && data.detail) || ("请求失败：" + response.status);
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return data;
}

async function loadMe() {
  try {
    const data = await api("/api/auth/me");
    if (!data.authenticated) {
      showLanding();
      return false;
    }
    currentUser = data.user;
    document.getElementById("user-label").textContent =
      (currentUser.display_name || currentUser.username) + " · " + currentUser.role;
    document.getElementById("side-user-name").textContent =
      currentUser.display_name || currentUser.username;
    document.getElementById("side-user-role").textContent = ROLE_NAMES[currentUser.role] || currentUser.role;
    document.getElementById("side-avatar").textContent =
      String(currentUser.display_name || currentUser.username || "S").slice(0, 1).toUpperCase();
    document.getElementById("logout").style.display = data.auth_enabled ? "inline-block" : "none";
    hideLanding();
    hideLogin();
    summaryToggleExport();
    return true;
  } catch (error) {
    showLanding();
    return false;
  }
}

async function doLogin() {
  const message = document.getElementById("login-msg");
  message.textContent = "";
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-SafeOrbit-CSRF": "1"},
      body: JSON.stringify({
        username: document.getElementById("login-user").value,
        password: document.getElementById("login-pass").value,
      }),
    });
    document.getElementById("login-pass").value = "";
    const data = await response.json();
    if (!response.ok) {
      message.textContent = data.detail || "登录失败";
      return;
    }
    if (await loadMe()) {
      refreshAll();
      connectEvents();
    }
  } catch (error) {
    message.textContent = "登录失败：" + error.message;
  }
}

async function doLogout() {
  resetModelSettings();
  clearAgentSecret();
  try { await api("/api/auth/logout", {method: "POST"}); } catch (error) {}
  currentUser = null;
  showLanding();
}

document.getElementById("landing-enter").addEventListener("click", showLogin);
document.getElementById("landing-enter-cta").addEventListener("click", showLogin);
document.getElementById("login-back").addEventListener("click", showLanding);
document.getElementById("landing-capabilities-jump").addEventListener("click", () => {
  const target = document.getElementById("landing-capabilities");
  const root = document.getElementById("landing");
  if (!target || !root) return;
  const offset = target.getBoundingClientRect().top - root.getBoundingClientRect().top + root.scrollTop - 20;
  root.scrollTo({top: Math.max(0, offset), behavior: reduceMotion.matches ? "auto" : "smooth"});
});
document.getElementById("login-button").addEventListener("click", doLogin);
document.getElementById("login-pass").addEventListener("keydown", event => {
  if (event.key === "Enter") doLogin();
});
document.getElementById("logout").addEventListener("click", doLogout);

// ------------------------- 标签页 -------------------------
let sysSection = "config";
let sysConfigTab = "models";
const PANEL_TO_MODULE = {
  guide: "guide", overview: "overview", traces: "runs", audit: "audit",
  dispositions: "approvals",
  policies: "policies", agents: "agents", settings: "models",
  codeaudit: "codeaudit", redteam: "redteam",
  agent: "agent", task: "task", account: "account",
};

function switchTab(name) {
  if (name !== 'settings' && modelSettings) closeModelForm();
  clearAgentSecret();
  document.querySelectorAll(".panel").forEach(panel => panel.classList.toggle("active", panel.id === "panel-" + name));
  if (name === "policies") {
    loadPolicies();
    const statusCard = document.getElementById("system-status-card");
    if (statusCard) statusCard.hidden = !can("admin");
  }
  if (name === "audit") applyAuditVisibility();
  if (name === "dispositions") loadDispositions();
  if (name === "codeaudit") loadCodeAudit();
  if (name === "agents") {
    loadAgents();
    const manageCard = document.getElementById("agent-management-card");
    if (manageCard) manageCard.open = true;
  }
  if (name === "settings") {
    loadModels();
    loadModelSettings();
    loadAuditParams();
    loadKbStatus();
    if (sysSection === "data") loadDataStats();
  }
  if (name === "guide") renderGuide();
  if (typeof afterPanelSwitch === "function") afterPanelSwitch(name);
  const body = document.getElementById("l3-body");
  if (body) body.scrollTop = 0;
}

// ------------------------- 轨迹 -------------------------
let traceOffset = 0;
const TRACE_LIMIT = 50;
let traceTotal = 0;

function traceFilterParams(offset) {
  const params = new URLSearchParams();
  params.set("limit", String(TRACE_LIMIT));
  params.set("offset", String(Math.max(0, offset)));
  const agent = document.getElementById("f-agent").value.trim();
  const verdict = document.getElementById("f-verdict").value;
  const search = document.getElementById("f-search").value.trim();
  const start = document.getElementById("f-start").value;
  const end = document.getElementById("f-end").value;
  if (agent) params.set("agent_id", agent);
  if (verdict) params.set("verdict", verdict);
  if (search) params.set("search", search);
  if (start) params.set("start", start);
  if (end) params.set("end", end);
  return params;
}

async function refreshTraces() {
  try {
    const data = await api("/api/traces?" + traceFilterParams(traceOffset).toString());
    const traces = data.traces || [];
    traceTotal = Number(data.total || 0);
    document.getElementById("mode").textContent =
      (data.mode === "shadow" ? "影子模式" : "enforce · 拦截模式") + " · 在线";
    document.getElementById("gateway-url").textContent = data.gateway_url || "";
    document.getElementById("trace-total").textContent = "共 " + traceTotal + " 条";
    document.getElementById("recent-count").textContent =
      traces.length ? "显示 " + traces.length + " 条" : "暂无记录";
    const page = Math.floor(traceOffset / TRACE_LIMIT) + 1;
    const pages = Math.max(1, Math.ceil(traceTotal / TRACE_LIMIT));
    document.getElementById("trace-page").textContent = "第 " + Math.min(page, pages) + " / " + pages + " 页";
    document.getElementById("trace-prev").disabled = traceOffset <= 0;
    document.getElementById("trace-next").disabled = traceOffset + TRACE_LIMIT >= traceTotal;
    const body = document.getElementById("traces");
    if (!traces.length) {
      body.innerHTML = '<tr><td colspan="6" class="empty">没有符合筛选条件的记录</td></tr>';
    } else {
      body.innerHTML = traces.map(t => {
        const verdict = LABELS[t.verdict] || t.verdict;
        return '<tr data-trace="' + esc(t.trace_id) + '" title="点击回放这条记录">' +
          '<td><code>' + esc(t.trace_id) + '</code>' +
            (Number(t.report_version || 1) > 1 ? ' <span class="muted">v' + Number(t.report_version) + '</span>' : '') + '</td>' +
          '<td>' + esc(t.agent_id) + '</td>' +
          '<td><span class="badge ' + esc(t.verdict) + '">' + esc(verdict) + '</span></td>' +
          '<td>' + Number(t.hits_count || 0) + '</td>' +
          '<td>' + money(t.cost_usd) + '</td>' +
          '<td class="muted">' + esc(timeText(t.created_at)) + '</td></tr>';
      }).join("");
    }
  } catch (error) {
    document.getElementById("mode").textContent = "连接失败";
  }
  const overview = await refreshSummary(false);
  if (overview) applyTotals(overview);
}

document.getElementById("f-apply").addEventListener("click", () => {
  traceOffset = 0;
  refreshTraces();
});
document.getElementById("f-reset").addEventListener("click", () => {
  ["f-agent", "f-search", "f-start", "f-end"].forEach(id => { document.getElementById(id).value = ""; });
  document.getElementById("f-verdict").value = "";
  traceOffset = 0;
  refreshTraces();
});
document.getElementById("f-export").addEventListener("click", () => {
  window.location.href = "/api/traces/export.csv?" + traceFilterParams(0).toString();
});
document.getElementById("trace-prev").addEventListener("click", () => {
  traceOffset = Math.max(0, traceOffset - TRACE_LIMIT);
  refreshTraces();
});
document.getElementById("trace-next").addEventListener("click", () => {
  if (traceOffset + TRACE_LIMIT < traceTotal) {
    traceOffset += TRACE_LIMIT;
    refreshTraces();
  }
});

// ------------------------- 汇总看板 -------------------------
let summaryCache = null;
let summaryLoadedAt = 0;
let summaryPending = null;
let summaryBusy = false;

function applyTotals(overview) {
  const totals = overview.totals || {};
  const verdicts = overview.verdicts || {};
  document.getElementById("stat-total").textContent = Number(totals.traces || 0);
  document.getElementById("stat-fail").textContent = Number(verdicts.fail || 0);
  document.getElementById("stat-warn").textContent =
    Number(verdicts.warn || 0) + Number(verdicts.review || 0);
  document.getElementById("stat-cost").textContent = money(totals.cost_usd);
}

function summaryToggleExport() {
  const button = document.getElementById("summary-export");
  if (!button) return;
  button.disabled = summaryBusy || !can("auditor");
}

function renderSummary(data) {
  const totals = data.totals || {};
  const verdicts = data.verdicts || {};
  const total = Number(totals.traces || 0);
  let scope = "全部历史记录 · " + total + " 条";
  if (totals.first_seen) scope += " · 自 " + String(totals.first_seen).slice(0, 10);
  if (data.detail_truncated) {
    scope += " · 明细已截断（上限 " + Number(data.detail_limit || 0) + " 条）";
  }
  document.getElementById("summary-scope").textContent = scope;

  const order = ["pass", "warn", "fail", "review"];
  document.getElementById("summary-verdicts").innerHTML = order.map(key => {
    const count = Number(verdicts[key] || 0);
    const ratio = total ? (count / total * 100) : 0;
    return '<tr><td><span class="badge ' + key + '">' + esc(LABELS[key] || key) + '</span></td>' +
      '<td>' + count + '</td><td>' + ratio.toFixed(1) + '%</td></tr>';
  }).join("");

  const agents = data.agents || [];
  document.getElementById("summary-agents").innerHTML = agents.length ? agents.map(a =>
    '<tr><td><code>' + esc(a.agent_id || "未知") + '</code></td>' +
    '<td>' + Number(a.traces || 0) + '</td>' +
    '<td>' + Number(a.risk_traces || 0) + '</td>' +
    '<td>' + Number(a.hits || 0) + '</td>' +
    '<td>' + money(a.cost_usd) + '</td>' +
    '<td class="muted">' + esc(timeText(a.latest_at)) + '</td></tr>'
  ).join("") : '<tr><td colspan="6" class="empty">暂无智能体数据</td></tr>';

  const daily = data.daily || [];
  const maxDaily = Math.max.apply(null, daily.map(d => Number(d.traces || 0)).concat([1]));
  document.getElementById("summary-trend").innerHTML = daily.length ? daily.map(d => {
    const width = (Number(d.traces || 0) / maxDaily * 100).toFixed(1);
    return '<tr><td>' + esc(d.date) + '</td><td>' + Number(d.traces || 0) + '</td>' +
      '<td>' + money(d.cost_usd) + '</td><td><div style="height:8px;background:#151b1f;border-radius:4px;overflow:hidden">' +
      '<div style="height:8px;background:var(--blue);width:' + width + '%"></div></div></td></tr>';
  }).join("") : '<tr><td colspan="4" class="empty">暂无趋势数据</td></tr>';

  const risks = data.risks || [];
  document.getElementById("summary-risks").innerHTML = risks.length ? risks.map(r =>
    '<tr><td><code>' + esc(r.trace_id) + '</code></td>' +
    '<td>' + esc(r.agent_id || "未知") + '</td>' +
    '<td><span class="badge ' + esc(r.verdict) + '">' + esc(LABELS[r.verdict] || r.verdict) + '</span></td>' +
    '<td>' + Number(r.hits_count || 0) + '</td>' +
    '<td>' + money(r.cost_usd) + '</td>' +
    '<td class="muted">' + esc(timeText(r.created_at)) + '</td>' +
    '<td>' + ((r.hits || []).map(h => '<div class="finding">[' + esc(h.severity) + '] <b>' +
      esc(h.rule_name) + '</b>：' + esc(h.message) + '</div>').join("") || '<span class="muted">无命中摘要</span>') +
    '</td></tr>'
  ).join("") : '<tr><td colspan="7" class="empty">没有不通过或待复核的记录</td></tr>';
}

async function refreshSummary(force) {
  const panel = document.getElementById("panel-traces");
  if (!panel || !panel.classList.contains("active")) return summaryCache;
  const now = Date.now();
  if (!force && summaryCache && now - summaryLoadedAt < 15000) return summaryCache;
  if (summaryPending) return summaryPending;
  summaryPending = api("/api/traces/overview").then(data => {
    summaryCache = data;
    summaryLoadedAt = Date.now();
    renderSummary(data);
    document.getElementById("summary-msg").textContent = "";
    return data;
  }).catch(error => {
    document.getElementById("summary-msg").textContent = "汇总加载失败：" + error.message;
    return summaryCache;
  }).finally(() => {
    summaryPending = null;
    summaryToggleExport();
  });
  return summaryPending;
}

async function exportSummary() {
  const button = document.getElementById("summary-export");
  const message = document.getElementById("summary-msg");
  if (!can("auditor")) {
    message.textContent = "需要 auditor 及以上角色才能生成汇总报告";
    return;
  }
  if (summaryBusy) return;
  summaryBusy = true;
  summaryToggleExport();
  message.textContent = "正在生成汇总报告…";
  try {
    const data = await api("/api/traces/summary-report", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: "{}",
    });
    const link = document.getElementById("summary-latest");
    link.href = data.view_url;
    link.hidden = false;
    link.textContent = "打开汇总报告 " + data.report_id + " →";
    message.textContent = "汇总报告已生成，正在下载";
    window.location.href = data.download_url;
  } catch (error) {
    if (error.status === 400) {
      message.textContent = "暂无可汇总的记录，先接入记录再导出";
    } else if (error.status === 403) {
      message.textContent = "权限不足：需要 auditor 及以上角色";
    } else {
      message.textContent = "生成失败：" + error.message;
    }
  } finally {
    summaryBusy = false;
    summaryToggleExport();
  }
}

function renderAlerts() {
  const box = document.getElementById("alerts");
  document.getElementById("alerts-count").textContent =
    alerts.length ? alerts.length + " 条提醒" : "暂无提醒";
  if (!alerts.length) return;
  box.innerHTML = alerts.slice(0, 12).map(item =>
    '<div class="alert">' +
      '<div class="top"><span class="sev sev-' + esc(item.severity) + '">' +
        esc(String(item.severity || "").toUpperCase()) + '</span>' +
        '<span class="badge ' + esc(item.verdict) + '">' + esc(LABELS[item.verdict] || item.verdict) + '</span>' +
        '<span class="muted">' + esc(timeText(item.created_at)) + '</span></div>' +
      '<div class="msg">' + esc(item.message) + '</div>' +
      '<div class="meta">' + esc(item.agent_id) + ' · <code>' + esc(item.trace_id) + '</code> · ' + money(item.cost_usd) + '</div>' +
    '</div>'
  ).join("");
}

const KIND_TEXT = {llm: "LLM", tool: "TOOL", agent: "AGENT", retrieval: "RAG", handoff: "HANDOFF", human: "HUMAN", system: "SYS"};
const ACTION_TEXT = {read: "读取", write: "写入", exec: "执行"};
let auditViewData = null;

async function openReplay(traceId) {
  showRunDetail(traceId);
  document.getElementById("replay-summary").textContent = "正在加载审计视图…";
  try {
    const data = await api("/api/traces/" + encodeURIComponent(traceId) + "/audit-views");
    auditViewData = data;
    replayData = {trace_id: data.trace_id, report_url: data.report_url, spans: data.timeline || []};
    spanDetailCache = null;
    closeDrawer();
    document.getElementById("integrity-result").textContent = "";
    renderReplay();
    renderAuditViews();
    renderCrew(traceId);
    switchReplayView("timeline");
    play();
  } catch (error) {}
}

async function renderCrew(traceId) {
  const box = document.getElementById("crew-panel");
  if (!box) return;
  box.hidden = true;
  box.innerHTML = "";
  try {
    const data = await api("/api/traces/" + encodeURIComponent(traceId) + "/crew");
    const agents = (data && data.agents) || [];
    if (!agents.length) return;
    const stateText = {ok: "已执行", skipped: "已跳过", error: "执行异常"};
    const cards = agents.map(agent => {
      const cites = (agent.citations || []).map(item =>
        '<span class="crew-cite">' + esc(item.title) +
        (item.section ? " / " + esc(item.section) : "") + "</span>"
      ).join("");
      return '<div class="crew-card">' +
        '<div class="crew-name"><span>' + esc(agent.name) + "</span>" +
        '<span class="crew-state">' + esc(stateText[agent.status] || agent.status) + "</span></div>" +
        '<div class="crew-meta">提出 ' + Number(agent.findings || 0) + " 条建议复核</div>" +
        (cites ? '<div class="crew-cites">依据：' + cites + "</div>" : "") +
        "</div>";
    }).join("");
    box.innerHTML = '<div class="crew-title">审计小队 · ' + agents.length +
      " 个 Agent 分工取证，结论统一交由裁决器</div>" +
      '<div class="crew-grid">' + cards + "</div>";
    box.hidden = false;
  } catch (error) {}
}

function renderReplay() {
  clearInterval(playTimer);
  playTimer = null;
  const data = replayData;
  document.getElementById("replay-trace").textContent = data.trace_id;
  document.getElementById("replay-report").href = data.report_url;
  document.getElementById("play").textContent = "播放";
  const summary = (auditViewData && auditViewData.summary) || {};
  document.getElementById("replay-summary").textContent =
    "共 " + Number(summary.steps || data.spans.length) + " 个步骤 · 工具 " + Number(summary.tools || 0) +
    " 次 · 写入 " + Number(summary.writes || 0) + " 次 · 阻断 " + Number(summary.blocked || 0) +
    " 次 · 触碰资源 " + Number(summary.resources || 0) + " 个";
  const track = document.getElementById("track");
  track.innerHTML = '<div class="playhead" id="playhead"></div>' + data.spans.map((s, i) =>
    '<div class="bar kind-' + esc(s.kind) + '" data-index="' + i + '" title="' + esc(s.name) + '" style="left:' +
    s.left_pct + '%;width:' + s.width_pct + '%"></div>'
  ).join("");
  document.getElementById("replay-list").innerHTML = data.spans.map((s, i) =>
    '<div class="rrow" data-index="' + i + '" tabindex="0">' +
      '<div class="kind">' + esc(KIND_TEXT[s.kind] || s.kind) + '</div>' +
      '<div><div class="name">' + esc(s.name) +
        ' <span class="muted">+' + Math.round(s.offset_ms) + 'ms · ' + Math.round(s.duration_ms) + 'ms</span></div>' +
      '<div class="sum">' + esc(s.summary) + '</div>' +
      (s.findings || []).map(f => '<div class="finding">[' + esc(f.severity) + '] <b>' +
        esc(f.rule_name) + '</b>：' + esc(f.message) + '</div>').join("") +
      '</div></div>'
  ).join("");
}

function renderAuditViews() {
  const data = auditViewData;
  if (!data) return;

  const resources = data.resources || [];
  document.getElementById("resource-grid").innerHTML = resources.length ? resources.map(item =>
    '<div class="res-card risk-' + esc(item.risk) + '">' +
      '<div class="res-head"><span>' + esc(item.kind_label) + '</span><span>' +
        esc(ACTION_TEXT[item.action] || item.action) + ' · ' + Number(item.count) + ' 次</span></div>' +
      '<div class="res-name">' + esc(item.resource) + '</div>' +
      '<div class="res-meta">工具：' + esc((item.tools || []).join("、") || "-") +
        (item.blocked ? ' · <span style="color:var(--red)">含阻断</span>' : '') +
        ((item.findings || []).length ? ' · 命中 ' + item.findings.length + ' 项' : '') + '</div>' +
      ((item.span_ids || []).length ?
        '<div class="res-actions"><button class="small" data-locate="' + esc(item.span_ids[0]) + '">定位步骤</button></div>' : '') +
    '</div>'
  ).join("") : '<div class="empty">本次没有工具调用，没有触碰外部资源。</div>';

  const permissions = data.permissions || [];
  document.getElementById("permission-list").innerHTML = permissions.length ? permissions.map(item =>
    '<div class="perm-row">' +
      '<div class="perm-meta">' + esc(timeText(item.at)) + '</div>' +
      '<div><b>' + esc(item.tool) + '</b> <span class="muted">· ' + esc(item.permission) + '</span>' +
        '<div class="perm-meta">来源：' + esc(item.source) +
        (item.declared_by_client ? ' · 客户端自报不可信' : '') +
        ((item.approval_ids || []).length ? ' · 票据 ' + esc(item.approval_ids.join("、")) : '') +
        '</div></div>' +
      '<div><span class="badge ' + (item.status === "已阻断" ? "fail" : "pass") + '">' + esc(item.status) + '</span></div>' +
    '</div>'
  ).join("") : '<div class="empty">没有工具调用，无需权限。</div>';

  const decisions = data.decisions || [];
  document.getElementById("decision-list").innerHTML = decisions.length ? decisions.map(item =>
    '<div class="chain-title"><span>' + esc(item.tool) + ' · ' + esc(timeText(item.at)) + '</span>' +
      '<span class="muted">参数指纹 ' + esc(item.arguments_hash) + '</span></div>' +
    '<div class="chain">' +
      '<div class="chain-step"><div class="label">前置提示</div><div class="text">' + esc(item.prompt) +
        (item.model ? ' <span class="muted">（' + esc(item.model) + '）</span>' : '') + '</div></div>' +
      '<div class="chain-step"><div class="label">上下文</div><div class="text">' + esc(item.context) + '</div></div>' +
      '<div class="chain-step"><div class="label">工具调用</div><div class="text">' + esc(item.arguments || "（无参数）") + '</div></div>' +
      '<div class="chain-step"><div class="label">结果</div><div class="text">' + esc(item.outcome) + '</div></div>' +
    '</div>'
  ).join("") : '<div class="empty">没有工具调用，无法还原决策链。</div>';

  const impact = data.impact || {};
  document.getElementById("impact-banner").innerHTML =
    '<div class="impact-banner">' + esc(impact.note || "") + '</div>';
  const items = impact.items || [];
  document.getElementById("impact-list").innerHTML = items.length ? items.map(item =>
    '<tr><td class="muted">' + esc(timeText(item.at)) + '</td><td><b>' + esc(item.tool) + '</b></td>' +
      '<td>' + esc(item.target_kind) + ' · <code>' + esc(item.target) + '</code></td>' +
      '<td class="muted">' + esc(item.result) + '</td>' +
      '<td><span class="badge ' + (item.blocked ? "fail" : "warn") + '">' + (item.blocked ? "已阻断" : "已写入") + '</span></td></tr>'
  ).join("") : '<tr><td colspan="5" class="empty">本次没有写入操作，未产生持久变更。</td></tr>';
}

function switchReplayView(name) {
  document.querySelectorAll("#replay-tabs .view-tab").forEach(tab => {
    tab.classList.toggle("active", tab.dataset.view === name);
  });
  ["timeline", "resources", "permissions", "decisions", "impact"].forEach(key => {
    const pane = document.getElementById("view-" + key);
    if (pane) pane.hidden = key !== name;
  });
}

function locateSpan(spanId) {
  switchReplayView("timeline");
  const index = (replayData && replayData.spans ? replayData.spans : []).findIndex(span => span.span_id === spanId);
  if (index >= 0) openSpanDetail(index);
}

document.getElementById("replay-tabs").addEventListener("click", event => {
  const tab = event.target.closest(".view-tab[data-view]");
  if (tab) switchReplayView(tab.dataset.view);
});
document.getElementById("resource-grid").addEventListener("click", event => {
  const button = event.target.closest("button[data-locate]");
  if (button) locateSpan(button.dataset.locate);
});

function setActive(index) {
  document.querySelectorAll("#replay-list .rrow").forEach((row, i) => row.classList.toggle("active", i === index));
  const bars = document.querySelectorAll("#track .bar");
  bars.forEach((bar, i) => bar.classList.toggle("active", i === index));
  const head = document.getElementById("playhead");
  if (bars[index] && head) head.style.left = bars[index].style.left;
}

function play() {
  if (!replayData || !replayData.spans.length) return;
  clearInterval(playTimer);
  playTimer = null;
  let index = 0;
  const button = document.getElementById("play");
  const step = () => {
    if (!replayData || index >= replayData.spans.length) {
      clearInterval(playTimer);
      playTimer = null;
      button.textContent = "重新播放";
      return;
    }
    setActive(index);
    index += 1;
  };
  button.textContent = "播放中…";
  step();
  playTimer = setInterval(step, 700);
}
document.getElementById("play").addEventListener("click", play);

let spanDetailCache = null;

async function openSpanDetail(index) {
  if (!replayData) return;
  try {
    if (!spanDetailCache) {
      spanDetailCache = await api("/api/traces/" + encodeURIComponent(replayData.trace_id) + "/spans");
    }
    const span = (spanDetailCache.spans || [])[index];
    if (!span) return;
    setActive(index);
    const toolName = span.name || (span.tool && (span.tool.name || span.tool.tool_name)) || "";
    const permissions = ((auditViewData && auditViewData.permissions) || []).filter(item => item.tool === toolName);
    document.getElementById("drawer-title").textContent = (KIND_TEXT[span.kind] || span.kind) + " · " + span.name;
    document.getElementById("drawer-body").innerHTML =
      '<div class="drawer-sec"><div class="drawer-label">命中</div>' +
        ((span.findings || []).length
          ? span.findings.map(f => '<div class="finding">[' + esc(f.severity) + '] <b>' + esc(f.rule_name) + '</b>：' + esc(f.message) + '</div>').join("")
          : '<div class="muted">该步骤未命中规则</div>') +
      '</div>' +
      '<div class="drawer-sec"><div class="drawer-label">来源</div><div class="kv">' +
        '<div class="k">步骤</div><div>' + esc(KIND_TEXT[span.kind] || span.kind) + ' · ' + esc(span.name) + '</div>' +
        '<div class="k">状态</div><div>' + esc(span.status || "-") + '</div>' +
        '<div class="k">耗时</div><div>' + Math.round(Number(span.duration_ms || 0)) + ' ms</div>' +
        '<div class="k">模型</div><div>' + esc((span.llm && (span.llm.model || span.llm.model_name)) || "-") + '</div>' +
        '<div class="k">证据引用</div><div><code>' + esc(span.evidence_ref || "-") + '</code></div>' +
      '</div></div>' +
      '<div class="drawer-sec"><div class="drawer-label">权限与授权</div>' +
        (permissions.length
          ? permissions.map(p => '<div class="kv" style="margin-bottom:8px">' +
              '<div class="k">工具</div><div>' + esc(p.tool) + '</div>' +
              '<div class="k">权限</div><div>' + esc(p.permission) + '</div>' +
              '<div class="k">来源</div><div>' + esc(p.source) + (p.declared_by_client ? ' · 客户端自报不可信' : '') + '</div>' +
              '<div class="k">状态</div><div>' + esc(p.status) + '</div></div>').join("")
          : '<div class="muted">该步骤没有工具调用，无需授权。</div>') +
      '</div>' +
      '<div class="drawer-sec"><div class="drawer-label">输入 / 输出 / 原始字段</div>' +
        '<pre style="white-space:pre-wrap;overflow-wrap:anywhere">' + esc(JSON.stringify({
          span_id: span.span_id, parent_span_id: span.parent_span_id, status: span.status,
          start_time: span.start_time, end_time: span.end_time, error: span.error,
          input: span.input, output: span.output, attributes: span.attributes,
          llm: span.llm, tool: span.tool, retrieval: span.retrieval,
          handoff: span.handoff, evidence_ref: span.evidence_ref,
        }, null, 2)) + '</pre></div>';
    openDrawer();
  } catch (error) {
    document.getElementById("drawer-title").textContent = "加载失败";
    document.getElementById("drawer-body").innerHTML = '<div class="muted">' + esc(error.message) + '</div>';
    openDrawer();
  }
}

document.getElementById("replay-list").addEventListener("click", event => {
  const row = event.target.closest(".rrow[data-index]");
  if (row) openSpanDetail(Number(row.dataset.index));
});

async function checkIntegrity() {
  if (!replayData) return;
  const box = document.getElementById("integrity-result");
  box.textContent = "校验中…";
  try {
    const data = await api("/api/traces/" + encodeURIComponent(replayData.trace_id) + "/integrity");
    const versions = data.versions || [];
    if (data.valid) {
      box.innerHTML = "证据链有效 · " + versions.length + " 个版本";
    } else {
      box.innerHTML = '<span style="color:var(--red)">证据链校验失败</span>';
    }
  } catch (error) {
    box.textContent = "校验失败：" + error.message;
  }
}
document.getElementById("integrity-check").addEventListener("click", checkIntegrity);
document.getElementById("traces").addEventListener("click", event => {
  const row = event.target.closest("tr[data-trace]");
  if (row) openReplay(row.dataset.trace);
});
document.getElementById("summary-refresh").addEventListener("click", () => {
  refreshSummary(true);
});
document.getElementById("summary-export").addEventListener("click", exportSummary);

// ------------------------- 策略 -------------------------
async function loadRules() {
  if (rulesCache.length) return;
  try {
    const data = await api("/api/rules");
    rulesCache = data.rules || [];
    document.getElementById("p-rules").innerHTML = rulesCache.map(rule =>
      '<label><input type="checkbox" value="' + esc(rule.id) + '"> ' +
      esc(rule.name) + ' <span class="muted">(' + esc(rule.severity) + ')</span></label>'
    ).join("");
  } catch (error) {}
}

function resetPolicyForm() {
  editingPolicyId = null;
  document.getElementById("policy-form-title").textContent = "新建策略";
  document.getElementById("p-agent").value = "";
  document.getElementById("p-name").value = "";
  document.getElementById("p-mode").value = "shadow";
  document.getElementById("p-budget").value = "";
  document.getElementById("p-tools").value = "";
  document.getElementById("p-threshold").value = "";
  document.getElementById("p-loop").value = "3";
  document.getElementById("p-confirm").value = "1";
  document.getElementById("p-redact").value = "0";
  document.getElementById("p-note").value = "";
  document.getElementById("policy-msg").textContent = "";
  document.querySelectorAll("#p-rules input").forEach(input => { input.checked = false; });
}

async function loadPolicies() {
  await loadRules();
  try {
    const data = await api("/api/policies");
    const policies = data.policies || [];
    const defaultPolicy = policies.find(item => item.agent_id === "*");
    const hint = document.getElementById("policy-mode-hint");
    if (data.mode === "enforce" && (!defaultPolicy || defaultPolicy.mode !== "enforce")) {
      hint.hidden = false;
      hint.textContent = "当前全局是“允许阻断”，但默认策略（*）仍是“仅记录”：没有单独配置策略的智能体不会被阻断。把默认策略改成“允许阻断”，所有智能体才会生效。";
    } else {
      hint.hidden = true;
    }
    document.getElementById("policy-count").textContent =
      policies.length ? policies.length + " 条策略" : "暂无策略";
    const body = document.getElementById("policies");
    if (!policies.length) {
      body.innerHTML = '<tr><td colspan="8" class="empty">暂无策略</td></tr>';
      return;
    }
    body.innerHTML = policies.map(p =>
      '<tr data-policy="' + esc(p.policy_id) + '">' +
        '<td><code>' + esc(p.agent_id) + '</code></td>' +
        '<td>' + esc(p.name) + '</td>' +
        '<td><span class="badge ' + (p.mode === "enforce" ? "fail" : "review") + '">' + esc(p.mode) + '</span></td>' +
        '<td>' + (p.budget_usd == null ? '<span class="muted">不限</span>' : money(p.budget_usd)) + '</td>' +
        '<td>' + (p.allowed_tools && p.allowed_tools.length ? esc(p.allowed_tools.join(", ")) : '<span class="muted">不限</span>') + '</td>' +
        '<td>' + (p.write_amount_threshold == null ? '<span class="muted">不限</span>' : esc(p.write_amount_threshold)) + '</td>' +
        '<td>' + (p.enabled_rules && p.enabled_rules.length ? esc(p.enabled_rules.length + " 条") : '<span class="muted">全部</span>') + '</td>' +
        '<td><button class="small" data-edit="' + esc(p.policy_id) + '">编辑</button> ' +
          '<button class="small danger" data-del="' + esc(p.policy_id) + '">删除</button></td>' +
      '</tr>'
    ).join("");
  } catch (error) {
    document.getElementById("policies").innerHTML = '<tr><td colspan="8" class="empty">' + esc(error.message) + '</td></tr>';
  }
}

function editPolicy(id) {
  api("/api/policies").then(data => {
    const policy = (data.policies || []).find(item => item.policy_id === id);
    if (!policy) return;
    editingPolicyId = id;
    document.getElementById("policy-form-title").textContent = "编辑策略 · " + policy.policy_id;
    document.getElementById("p-agent").value = policy.agent_id;
    document.getElementById("p-name").value = policy.name || "";
    document.getElementById("p-mode").value = policy.mode;
    document.getElementById("p-budget").value = policy.budget_usd == null ? "" : policy.budget_usd;
    document.getElementById("p-tools").value = (policy.allowed_tools || []).join(",");
    document.getElementById("p-threshold").value = policy.write_amount_threshold == null ? "" : policy.write_amount_threshold;
    document.getElementById("p-loop").value = policy.loop_threshold;
    document.getElementById("p-confirm").value = policy.require_confirmation_for_writes ? "1" : "0";
    document.getElementById("p-redact").value = policy.redact_content ? "1" : "0";
    document.getElementById("p-note").value = policy.note || "";
    const enabled = policy.enabled_rules || [];
    document.querySelectorAll("#p-rules input").forEach(input => {
      input.checked = enabled.includes(input.value);
    });
    document.getElementById("policy-form").open = true;
    document.getElementById("policy-form-title").scrollIntoView({behavior: "smooth", block: "center"});
  });
}

function policyPayload() {
  return {
    agent_id: document.getElementById("p-agent").value.trim() || "*",
    name: document.getElementById("p-name").value.trim(),
    mode: document.getElementById("p-mode").value,
    budget_usd: document.getElementById("p-budget").value || null,
    allowed_tools: document.getElementById("p-tools").value,
    write_amount_threshold: document.getElementById("p-threshold").value || null,
    loop_threshold: Number(document.getElementById("p-loop").value || 3),
    require_confirmation_for_writes: document.getElementById("p-confirm").value === "1",
    redact_content: document.getElementById("p-redact").value === "1",
    enabled_rules: Array.from(document.querySelectorAll("#p-rules input:checked")).map(input => input.value),
    note: document.getElementById("p-note").value.trim(),
  };
}

async function savePolicy() {
  const message = document.getElementById("policy-msg");
  message.textContent = "";
  const payload = policyPayload();
  try {
    if (editingPolicyId) {
      await api("/api/policies/" + encodeURIComponent(editingPolicyId), {
        method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
      });
    } else {
      await api("/api/policies", {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
      });
    }
    message.textContent = "已保存";
    resetPolicyForm();
    loadPolicies();
  } catch (error) {
    if (error.status === 409) {
      const data = await api("/api/policies");
      const existing = (data.policies || []).find(item => item.agent_id === payload.agent_id);
      if (existing) {
        editPolicy(existing.policy_id);
        message.textContent = "该智能体已有策略，已切换为编辑现有策略";
        return;
      }
    }
    message.textContent = error.message;
  }
}

async function simulatePolicy() {
  const message = document.getElementById("policy-msg");
  const result = document.getElementById("policy-sim-result");
  message.textContent = "试算中…";
  result.hidden = true;
  try {
    const data = await api("/api/policies/simulate", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(Object.assign(policyPayload(), {limit: 100})),
    });
    const counts = data.counts || {};
    const lines = [
      "扫描 " + data.scanned + " 条记录 · 全局模式 " + data.global_mode + " · 本策略生效模式 " + data.effective_mode,
      "判定分布：通过 " + (counts.pass || 0) + " · 有风险 " + (counts.warn || 0) +
        " · 不通过 " + (counts.fail || 0) + " · 待复核 " + (counts.review || 0),
      "切 enforce 预计阻断 " + data.would_block + " 条 · 触发复核/告警 " + data.would_review + " 条",
    ];
    const samples = data.samples || [];
    if (samples.length) {
      lines.push("影响样例（最多 20 条）：");
      samples.forEach(item => {
        lines.push("· [" + String(item.severity).toUpperCase() + "] " + item.trace_id + " · " +
          item.rule_name + "：" + item.message + (item.span_ref ? "（span " + item.span_ref + "）" : ""));
      });
    }
    result.textContent = lines.join("\\n");
    result.hidden = false;
    message.textContent = "试算完成";
  } catch (error) {
    message.textContent = "试算失败：" + error.message;
  }
}
document.getElementById("policy-simulate").addEventListener("click", simulatePolicy);

async function deletePolicy(id) {
  if (!window.confirm("确认删除策略 " + id + "？")) return;
  try {
    await api("/api/policies/" + encodeURIComponent(id), {method: "DELETE"});
    loadPolicies();
  } catch (error) {
    alert(error.message);
  }
}

document.getElementById("policy-save").addEventListener("click", savePolicy);
document.getElementById("policy-reset").addEventListener("click", resetPolicyForm);
document.getElementById("policies").addEventListener("click", event => {
  const edit = event.target.closest("button[data-edit]");
  if (edit) { editPolicy(edit.dataset.edit); return; }
  const del = event.target.closest("button[data-del]");
  if (del) deletePolicy(del.dataset.del);
});

// ------------------------- 处置 -------------------------
async function loadDispositions() {
  try {
    const data = await api("/api/dispositions");
    renderRuntime(data.runtime || {});
    loadApprovals();
    const items = data.dispositions || [];
    const body = document.getElementById("dispositions");
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="7" class="empty">暂无处置提案</td></tr>';
      return;
    }
    body.innerHTML = items.map(item => {
      const buttons = [];
      if (item.status === "proposed") {
        buttons.push('<button class="small" data-decide="' + esc(item.disposition_id) + '" data-approved="1"' +
          (can("auditor") ? "" : " disabled") + '>批准</button>');
        buttons.push('<button class="small" data-decide="' + esc(item.disposition_id) + '" data-approved="0"' +
          (can("auditor") ? "" : " disabled") + '>驳回</button>');
      }
      if (item.status === "approved") {
        buttons.push('<button class="small primary" data-execute="' + esc(item.disposition_id) + '"' +
          (can("operator") ? "" : " disabled") + '>执行</button>');
      }
      if (item.status === "executed") {
        buttons.push('<button class="small" data-effect="' + esc(item.disposition_id) + '">回检</button>');
      }
      return '<tr>' +
        '<td class="muted">' + esc(timeText(item.created_at)) + '</td>' +
        '<td>' + esc(item.agent_id) + '</td>' +
        '<td><b>' + esc(item.action_text || item.action) + '</b>' +
          (item.target ? '<div class="muted">目标：' + esc(item.target) + '</div>' : '') + '</td>' +
        '<td>' + esc(item.reason) + (item.execution_note ? '<div class="muted">' + esc(item.execution_note) + '</div>' : '') + '</td>' +
        '<td><span class="sev sev-' + esc(item.severity) + '">' + esc(String(item.severity).toUpperCase()) + '</span></td>' +
        '<td><span class="badge ' + (item.status === "executed" ? "pass" : item.status === "rejected" ? "fail" : "review") + '">' +
          esc(item.status_text || item.status) + '</span></td>' +
        '<td>' + buttons.join(" ") + '</td>' +
      '</tr>';
    }).join("");
  } catch (error) {
    document.getElementById("dispositions").innerHTML = '<tr><td colspan="7" class="empty">' + esc(error.message) + '</td></tr>';
  }
}

let approvalCache = [];
function approvalRisk(item) {
  const name = String(item.tool_name || "").toLowerCase();
  if (/(delete|drop|truncate|remove|refund|transfer|payout|pay|revoke)/.test(name)) {
    return {level: "high", text: "极高风险"};
  }
  if (/(update|create|write|send|publish|approve|grant)/.test(name)) {
    return {level: "high", text: "高风险"};
  }
  return {level: "medium", text: "需确认"};
}
async function loadApprovals() {
  try {
    const data = await api("/api/approvals");
    const items = data.approvals || [];
    approvalCache = items;
    const pending = items.filter(item => item.status === "proposed").length;
    const decided = items.filter(item => item.decided_at);
    const avgSeconds = decided.length
      ? decided.reduce((sum, item) => sum + Math.max(0, (Date.parse(item.decided_at) - Date.parse(item.created_at)) / 1000), 0) / decided.length
      : null;
    document.getElementById("approvals-count").textContent =
      (pending ? pending + " 条待审批" : "暂无待审批") +
      (avgSeconds === null ? "" : " · 平均决策 " + secondsText(avgSeconds));
    const body = document.getElementById("approvals");
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="7" class="empty">暂无待审批的写操作</td></tr>';
      return;
    }
    body.innerHTML = items.map(item => {
      const risk = approvalRisk(item);
      const buttons = [];
      if (item.status === "proposed") {
        buttons.push('<button class="small primary" data-approval="' + esc(item.approval_id) + '" data-approved="1"' +
          (can("operator") ? "" : " disabled") + '>批准</button>');
        buttons.push('<button class="small danger" data-approval="' + esc(item.approval_id) + '" data-approved="0"' +
          (can("operator") ? "" : " disabled") + '>驳回</button>');
      }
      const badge = item.status === "approved" ? "pass" : item.status === "rejected" ? "fail" : "review";
      return '<tr data-approval-row="' + esc(item.approval_id) + '" title="点击查看审批详情">' +
        '<td class="muted">' + esc(timeText(item.created_at)) + '</td>' +
        '<td>' + esc(item.agent_id) + '</td>' +
        '<td><b>' + esc(item.tool_name) + '</b><span class="approval-risk ' + risk.level + '">' + risk.text + '</span>' +
          '<div class="muted"><code>' + esc(item.approval_id) + '</code>' +
          (item.trace_id ? ' · <a href="#" data-trace-link="' + esc(item.trace_id) + '">查看轨迹</a>' : '') +
          '</div></td>' +
        '<td><span class="badge ' + badge + '">' + esc(item.status_text || item.status) + '</span></td>' +
        '<td>' + esc(item.decided_by || "-") + '</td>' +
        '<td class="muted">' + esc(timeText(item.expires_at)) + '</td>' +
        '<td>' + buttons.join(" ") +
          (item.status === "proposed" ?
            '<input class="approval-note" data-note-for="' + esc(item.approval_id) + '" maxlength="200" ' +
            'placeholder="审批理由（极高/高风险必填）">' : '') +
        '</td>' +
      '</tr>';
    }).join("");
  } catch (error) {
    document.getElementById("approvals").innerHTML = '<tr><td colspan="7" class="empty">' + esc(error.message) + '</td></tr>';
  }
}

async function decideApproval(id, approved, noteOverride) {
  if (!id) return;
  const item = approvalCache.find(entry => entry.approval_id === id);
  const input = document.querySelector('[data-note-for="' + id + '"]');
  const note = noteOverride !== undefined ? String(noteOverride || "").trim() : (input ? input.value.trim() : "");
  if (approved && item && approvalRisk(item).level === "high" && !note) {
    if (input) { input.focus(); input.style.borderColor = "var(--red)"; }
    const msg = document.getElementById("apd-msg");
    if (msg) msg.textContent = "高风险操作必须填写审批理由后才能批准";
    document.getElementById("approvals-count").textContent = "高风险操作必须填写审批理由后才能批准";
    return;
  }
  try {
    await api("/api/approvals/" + encodeURIComponent(id) + "/decide", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        approved: approved,
        note: note || (approved ? "人工批准写操作" : "人工驳回写操作"),
        ttl_minutes: 15,
      }),
    });
    await loadApprovals();
    if (typeof selectedApprovalId !== "undefined" && selectedApprovalId === id) {
      const updated = approvalCache.find(entry => entry.approval_id === id);
      if (updated) showApprovalDetail(id);
      else document.getElementById("approval-detail").hidden = true;
    }
  } catch (error) { alert(error.message); }
}

let selectedApprovalId = null;

function countdownText(value) {
  if (!value) return "—";
  const diff = Date.parse(value) - Date.now();
  if (Number.isNaN(diff)) return "—";
  if (diff <= 0) return "已过期";
  const total = Math.floor(diff / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return (minutes >= 100 ? minutes : String(minutes).padStart(2, "0")) + ":" + String(seconds).padStart(2, "0");
}

function updateApprovalCountdowns() {
  document.querySelectorAll("[data-expires]").forEach(node => {
    node.textContent = countdownText(node.dataset.expires);
  });
  const label = document.getElementById("apd-countdown");
  if (label && label.dataset.expires) label.textContent = "剩余 " + countdownText(label.dataset.expires);
}

async function showApprovalDetail(id) {
  const item = approvalCache.find(entry => entry.approval_id === id);
  if (!item) return;
  selectedApprovalId = id;
  document.querySelectorAll("#approvals tr[data-approval-row]").forEach(row => {
    row.classList.toggle("active-row", row.dataset.approvalRow === id);
  });
  const risk = approvalRisk(item);
  const card = document.getElementById("approval-detail");
  card.hidden = false;
  document.getElementById("apd-title").textContent = item.approval_id;
  const riskNode = document.getElementById("apd-risk");
  riskNode.textContent = risk.text;
  riskNode.className = "badge " + (risk.level === "high" ? "fail" : "review");
  const countdown = document.getElementById("apd-countdown");
  countdown.dataset.expires = item.expires_at || "";
  countdown.textContent = item.status === "proposed" ? "剩余 " + countdownText(item.expires_at) : "";
  document.getElementById("apd-diff").textContent =
    "工具：" + item.tool_name + "\\n参数指纹：" + item.arguments_hash +
    "\\n会话：" + (item.session_id || "-") + "\\n记录：" + (item.trace_id || "-") + "\\n\\n正在加载原始参数…";
  document.getElementById("apd-impact").innerHTML = [
    ["智能体", item.agent_id || "-"],
    ["工具", item.tool_name || "-"],
    ["关联记录", item.trace_id || "无"],
    ["发起时间", item.created_at ? timeText(item.created_at) : "-"],
    ["有效期至", item.expires_at ? timeText(item.expires_at) : "-"],
    ["状态", item.status_text || item.status || "-"],
    ["决策人", item.decided_by || "-"],
    ["参数指纹", item.arguments_hash || "-"],
  ].map(pair => '<div class="row"><span class="muted">' + esc(pair[0]) + '</span><span>' + esc(pair[1]) + '</span></div>').join("");
  document.getElementById("apd-msg").textContent = "";
  document.getElementById("apd-note").value = "";
  if (item.trace_id) {
    try {
      const data = await api("/api/traces/" + encodeURIComponent(item.trace_id) + "/audit-views");
      const decision = (data.decisions || []).find(entry => entry.tool === item.tool_name);
      document.getElementById("apd-diff").textContent =
        "工具：" + item.tool_name + "\\n\\n参数：\\n" +
        ((decision && decision.arguments) || "（该记录未保存原始参数，仅保留指纹）");
    } catch (error) {
      document.getElementById("apd-diff").textContent =
        "工具：" + item.tool_name + "\\n参数指纹：" + item.arguments_hash + "\\n\\n（原始参数读取失败：" + error.message + "）";
    }
  }
}

async function openApprovalChain() {
  const item = approvalCache.find(entry => entry.approval_id === selectedApprovalId);
  if (!item) return;
  document.getElementById("drawer-title").textContent = "决策链 · " + item.approval_id;
  let chain = "";
  if (item.trace_id) {
    try {
      const data = await api("/api/traces/" + encodeURIComponent(item.trace_id) + "/audit-views");
      const decision = (data.decisions || []).find(entry => entry.tool === item.tool_name);
      if (decision) {
        chain =
          '<div class="drawer-sec"><div class="drawer-label">前置提示</div><div>' + esc(decision.prompt || "-") +
            (decision.model ? ' <span class="muted">（' + esc(decision.model) + '）</span>' : '') + '</div></div>' +
          '<div class="drawer-sec"><div class="drawer-label">上下文</div><div>' + esc(decision.context || "-") + '</div></div>' +
          '<div class="drawer-sec"><div class="drawer-label">工具调用</div><pre style="white-space:pre-wrap;overflow-wrap:anywhere">' + esc(decision.arguments || "（无参数）") + '</pre></div>' +
          '<div class="drawer-sec"><div class="drawer-label">结果</div><div>' + esc(decision.outcome || "-") + '</div></div>';
      }
    } catch (error) {
      chain = '<div class="drawer-sec"><div class="muted">决策链读取失败：' + esc(error.message) + '</div></div>';
    }
  }
  document.getElementById("drawer-body").innerHTML =
    '<div class="drawer-sec"><div class="drawer-label">审批单</div><div class="kv">' +
      '<div class="k">智能体</div><div>' + esc(item.agent_id) + '</div>' +
      '<div class="k">工具</div><div>' + esc(item.tool_name) + '</div>' +
      '<div class="k">参数指纹</div><div><code>' + esc(item.arguments_hash) + '</code></div>' +
      '<div class="k">发起</div><div>' + esc(timeText(item.created_at)) + '</div>' +
      '<div class="k">状态</div><div>' + esc(item.status_text || item.status) + '</div>' +
    '</div></div>' + chain;
  openDrawer();
}

async function createManualDisposition() {
  const message = document.getElementById("d-msg");
  const agent = document.getElementById("d-agent").value.trim();
  const reason = document.getElementById("d-reason").value.trim();
  if (!agent || !reason) {
    message.textContent = "智能体与原因必须填写";
    return;
  }
  try {
    await api("/api/dispositions", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        agent_id: agent,
        action: document.getElementById("d-action").value,
        target: document.getElementById("d-target").value.trim() || null,
        session_id: document.getElementById("d-session").value.trim() || null,
        severity: document.getElementById("d-severity").value,
        reason: reason,
      }),
    });
    message.textContent = "提案已提交";
    document.getElementById("d-reason").value = "";
    loadDispositions();
  } catch (error) {
    message.textContent = error.message;
  }
}

async function showDispositionEffect(id) {
  try {
    const data = await api("/api/dispositions/" + encodeURIComponent(id) + "/effect");
    window.alert(
      (data.note || "") +
      "\\n观察后续轨迹：" + Number(data.observed || 0) +
      " 条，其中风险轨迹 " + Number(data.risk_traces || 0) + " 条"
    );
  } catch (error) { alert(error.message); }
}

document.getElementById("approvals").addEventListener("click", event => {
  const link = event.target.closest("a[data-trace-link]");
  if (link) {
    event.preventDefault();
    switchTab("traces");
    openReplay(link.dataset.traceLink);
    return;
  }
  const button = event.target.closest("button[data-approval]");
  if (button) { decideApproval(button.dataset.approval, button.dataset.approved === "1"); return; }
  const row = event.target.closest("tr[data-approval-row]");
  if (row) showApprovalDetail(row.dataset.approvalRow);
});
document.getElementById("apd-approve").addEventListener("click", () => {
  decideApproval(selectedApprovalId, true, document.getElementById("apd-note").value);
});
document.getElementById("apd-reject").addEventListener("click", () => {
  decideApproval(selectedApprovalId, false, document.getElementById("apd-note").value);
});
document.getElementById("apd-chain").addEventListener("click", openApprovalChain);
setInterval(updateApprovalCountdowns, 1000);
document.getElementById("d-create").addEventListener("click", createManualDisposition);

function renderRuntime(runtime) {
  const isolated = runtime.isolated_sessions || [];
  const tools = runtime.blocked_tools || [];
  document.getElementById("runtime-isolated-count").textContent =
    isolated.length ? isolated.length + " 个" : "0";
  document.getElementById("runtime-tools-count").textContent =
    tools.length ? tools.length + " 个" : "0";
  document.getElementById("runtime-isolated").innerHTML = isolated.length
    ? isolated.map(item => '<div class="alert"><div class="msg"><code>' + esc(item.session_id) + '</code></div>' +
        '<div class="meta">' + esc(item.agent_id) + ' · ' + esc(item.reason) + ' · 至 ' + esc(timeText(item.until)) +
        ' <button class="small" data-release-session="' + esc(item.session_id) + '" data-agent="' + esc(item.agent_id) + '">解除隔离</button></div></div>').join("")
    : '<div class="empty">暂无隔离会话</div>';
  document.getElementById("runtime-tools").innerHTML = tools.length
    ? tools.map(item => '<span class="chip">' + esc(item.agent_id) + ' / ' + esc(item.tool_name) +
        ' <button class="small" data-release-tool="' + esc(item.tool_name) + '" data-agent="' + esc(item.agent_id) + '">解除</button></span>').join("")
    : '<div class="empty">暂无阻断工具</div>';
}

async function releaseRuntime(payload, label) {
  if (!window.confirm("确认解除 " + label + "？")) return;
  try {
    await api("/api/runtime/release", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    loadDispositions();
  } catch (error) { alert(error.message); }
}

document.getElementById("runtime-isolated").addEventListener("click", event => {
  const button = event.target.closest("button[data-release-session]");
  if (button) releaseRuntime(
    {agent_id: button.dataset.agent, session_id: button.dataset.releaseSession},
    "会话 " + button.dataset.releaseSession
  );
});
document.getElementById("runtime-tools").addEventListener("click", event => {
  const button = event.target.closest("button[data-release-tool]");
  if (button) releaseRuntime(
    {agent_id: button.dataset.agent, tool_name: button.dataset.releaseTool},
    "工具 " + button.dataset.releaseTool
  );
});

async function decideDisposition(id, approved) {
  try {
    await api("/api/dispositions/" + encodeURIComponent(id) + "/decide", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({approved: approved, note: approved ? "人工批准" : "人工驳回"}),
    });
    loadDispositions();
  } catch (error) { alert(error.message); }
}

async function executeDisposition(id) {
  try {
    await api("/api/dispositions/" + encodeURIComponent(id) + "/execute", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({isolation_minutes: 30}),
    });
    loadDispositions();
  } catch (error) { alert(error.message); }
}

document.getElementById("dispositions").addEventListener("click", event => {
  const decide = event.target.closest("button[data-decide]");
  if (decide) { decideDisposition(decide.dataset.decide, decide.dataset.approved === "1"); return; }
  const execute = event.target.closest("button[data-execute]");
  if (execute) { executeDisposition(execute.dataset.execute); return; }
  const effect = event.target.closest("button[data-effect]");
  if (effect) showDispositionEffect(effect.dataset.effect);
});

// ------------------------- 代码审计 -------------------------
function renderCodeReport(report) {
  document.getElementById("code-latest-link").href = "/api/codeaudit/" + encodeURIComponent(report.report_id) + "/html";
  const summary = report.summary || {};
  document.getElementById("code-summary").textContent =
    "扫描 " + report.files_scanned + " 个文件 · critical " + (summary.critical || 0) +
    " · high " + (summary.high || 0) + " · medium " + (summary.medium || 0) +
    " · low " + (summary.low || 0);
  const body = document.getElementById("code-findings");
  if (!report.findings || !report.findings.length) {
    body.innerHTML = '<tr><td colspan="4" class="empty">未发现匹配规则的问题</td></tr>';
    return;
  }
  const colors = {critical: "fail", high: "fail", medium: "warn", low: "review", info: "review"};
  body.innerHTML = report.findings.map(item =>
    '<tr>' +
      '<td><span class="badge ' + (colors[item.severity] || "review") + '">' + esc(String(item.severity).toUpperCase()) + '</span></td>' +
      '<td><b>' + esc(item.rule_name) + '</b><div class="muted">' + esc(item.message) + '</div></td>' +
      '<td><code>' + esc(item.file) + ':' + item.line + '</code><div class="muted">' + esc(item.snippet) + '</div></td>' +
      '<td>' + esc(item.remediation) + '<div class="muted">' + esc(item.cwe) + '</div></td>' +
    '</tr>'
  ).join("");
}

async function loadCodeAudit() {
  try {
    const data = await api("/api/codeaudit/latest");
    renderCodeReport(data.report);
  } catch (error) {}
}

async function scanCode() {
  const path = document.getElementById("code-path").value.trim();
  const message = document.getElementById("code-msg");
  if (!path) { message.textContent = "请填写扫描路径"; return; }
  message.textContent = "扫描中…";
  try {
    const data = await api("/api/codeaudit/scan", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: path}),
    });
    message.textContent = "扫描完成";
    renderCodeReport(data.report);
  } catch (error) {
    message.textContent = error.message;
  }
}
document.getElementById("code-scan").addEventListener("click", scanCode);

// ------------------------- 代码检查：拖拽上传 -------------------------
const codeDropzone = document.getElementById("code-dropzone");
const codeFileInput = document.getElementById("code-file");
const codeFolderInput = document.getElementById("code-folder");
const CODE_UPLOAD_MAX_FILES = 2000;
const CODE_UPLOAD_MAX_BYTES = 50 * 1024 * 1024;

function codeUploadMessage(text) {
  document.getElementById("code-msg").textContent = text;
}

function codeSetBusy(busy) {
  codeDropzone.setAttribute("aria-busy", busy ? "true" : "false");
  ["code-scan", "code-folder-btn"].forEach(id => {
    const element = document.getElementById(id);
    if (element) element.disabled = busy;
  });
}

async function uploadCodeFiles(items) {
  if (!items || !items.length) { codeUploadMessage("没有读取到文件"); return; }
  if (items.length > CODE_UPLOAD_MAX_FILES) {
    codeUploadMessage("文件太多（上限 " + CODE_UPLOAD_MAX_FILES + " 个），建议先打包成 zip");
    return;
  }
  const total = items.reduce((sum, item) => sum + (item.file.size || 0), 0);
  if (total > CODE_UPLOAD_MAX_BYTES) {
    codeUploadMessage("总大小超过 50MB，建议只上传源码目录或压缩包");
    return;
  }
  const form = new FormData();
  const paths = [];
  items.forEach(item => {
    form.append("files", item.file, item.file.name);
    paths.push(item.path);
  });
  form.append("paths", JSON.stringify(paths));
  codeSetBusy(true);
  codeUploadMessage("上传并扫描中… " + items.length + " 个文件");
  try {
    const data = await api("/api/codeaudit/upload", {method: "POST", body: form});
    renderCodeReport(data.report);
    codeUploadMessage("扫描完成：" + data.report.files_scanned + " 个文件");
  } catch (error) {
    codeUploadMessage(error.message);
  } finally {
    codeSetBusy(false);
  }
}

async function walkDropEntry(entry, prefix, out) {
  if (out.length >= CODE_UPLOAD_MAX_FILES || !entry) return;
  if (entry.isFile) {
    const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
    out.push({file, path: prefix + entry.name});
    return;
  }
  if (!entry.isDirectory) return;
  const reader = entry.createReader();
  while (true) {
    const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
    if (!batch.length) break;
    for (const child of batch) await walkDropEntry(child, prefix + entry.name + "/", out);
  }
}

function filesFromInput(input) {
  return Array.from(input.files || []).map(file => ({
    file,
    path: file.webkitRelativePath || file.name,
  }));
}

codeDropzone.addEventListener("click", () => codeFileInput.click());
codeDropzone.addEventListener("keydown", event => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    codeFileInput.click();
  }
});
["dragenter", "dragover"].forEach(name => codeDropzone.addEventListener(name, event => {
  event.preventDefault();
  event.stopPropagation();
  codeDropzone.classList.add("dragover");
}));
["dragleave", "dragend"].forEach(name => codeDropzone.addEventListener(name, () => {
  codeDropzone.classList.remove("dragover");
}));
codeDropzone.addEventListener("drop", async event => {
  event.preventDefault();
  event.stopPropagation();
  codeDropzone.classList.remove("dragover");
  if (codeDropzone.getAttribute("aria-busy") === "true") return;
  const files = Array.from(event.dataTransfer.files || []);
  if (files.length === 1 && /\\.zip$/i.test(files[0].name)) {
    await uploadCodeFiles([{file: files[0], path: files[0].name}]);
    return;
  }
  const entries = [];
  if (event.dataTransfer.items && event.dataTransfer.items.length &&
      event.dataTransfer.items[0].webkitGetAsEntry) {
    for (const item of event.dataTransfer.items) {
      const entry = item.webkitGetAsEntry();
      if (entry) entries.push(entry);
    }
  }
  if (!entries.length) {
    await uploadCodeFiles(files.map(file => ({file, path: file.webkitRelativePath || file.name})));
    return;
  }
  const collected = [];
  for (const entry of entries) await walkDropEntry(entry, "", collected);
  await uploadCodeFiles(collected);
});
window.addEventListener("dragover", event => event.preventDefault());
window.addEventListener("drop", event => {
  if (!codeDropzone.contains(event.target)) event.preventDefault();
});

codeFileInput.addEventListener("change", async () => {
  const items = filesFromInput(codeFileInput);
  codeFileInput.value = "";
  await uploadCodeFiles(items);
});
document.getElementById("code-folder-btn").addEventListener("click", () => codeFolderInput.click());
codeFolderInput.addEventListener("change", async () => {
  const items = filesFromInput(codeFolderInput);
  codeFolderInput.value = "";
  await uploadCodeFiles(items);
});

function _severityBadge(value) {
  const colors = {critical: "fail", high: "fail", medium: "warn", low: "review", info: "review"};
  return '<span class="badge ' + (colors[value] || "review") + '">' + esc(String(value).toUpperCase()) + '</span>';
}

function renderSca(report) {
  const body = document.getElementById("sca-findings");
  document.getElementById("sca-msg").textContent =
    "依赖 " + (report.components || []).length + " 个 · 漏洞 " + (report.findings || []).length + " 个";
  if (!report.findings || !report.findings.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty">未发现已知漏洞</td></tr>';
    return;
  }
  body.innerHTML = report.findings.map(item =>
    '<tr>' +
      '<td>' + _severityBadge(item.severity) + '</td>' +
      '<td><b>' + esc(item.package) + '</b></td>' +
      '<td>' + esc(item.version) + '</td>' +
      '<td><code>' + esc(item.advisory_id) + '</code><div class="muted">' + esc(item.summary) + '</div></td>' +
      '<td>' + esc(item.fixed_version || "—") + '</td>' +
      '<td><code>' + esc(item.file) + ':' + item.line + '</code></td>' +
    '</tr>'
  ).join("");
}

async function runSca() {
  const path = document.getElementById("sca-path").value.trim();
  const message = document.getElementById("sca-msg");
  if (!path) { message.textContent = "请填写项目路径"; return; }
  message.textContent = "扫描中…";
  try {
    const data = await api("/api/sca/scan", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: path}),
    });
    renderSca(data.report);
  } catch (error) { message.textContent = error.message; }
}

function renderInfra(report) {
  const body = document.getElementById("infra-findings");
  document.getElementById("infra-msg").textContent =
    "文件 " + (report.files_scanned || 0) + " 个 · 问题 " + (report.findings || []).length + " 个";
  if (!report.findings || !report.findings.length) {
    body.innerHTML = '<tr><td colspan="4" class="empty">未发现高风险配置</td></tr>';
    return;
  }
  body.innerHTML = report.findings.map(item =>
    '<tr>' +
      '<td>' + _severityBadge(item.severity) + '</td>' +
      '<td><b>' + esc(item.rule_name) + '</b><div class="muted">' + esc(item.message) + '</div></td>' +
      '<td><code>' + esc(item.file) + ':' + item.line + '</code><div class="muted">' + esc(item.snippet) + '</div></td>' +
      '<td>' + esc(item.remediation) + '</td>' +
    '</tr>'
  ).join("");
}

async function runInfra() {
  const path = document.getElementById("infra-path").value.trim();
  const message = document.getElementById("infra-msg");
  if (!path) { message.textContent = "请填写项目路径"; return; }
  message.textContent = "扫描中…";
  try {
    const data = await api("/api/infrascan/scan", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: path}),
    });
    renderInfra(data.report);
  } catch (error) { message.textContent = error.message; }
}

async function runRedteam() {
  const message = document.getElementById("redteam-msg");
  const body = document.getElementById("redteam-findings");
  message.textContent = "检测中…";
  try {
    const data = await api("/api/eval/redteam", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: "{}",
    });
    const report = data.report || {};
    message.textContent = "检出率 " + Math.round(Number(report.detection_rate || 0) * 100) +
      "%（" + Number(report.detected || 0) + "/" + Number(report.total || 0) + "）";
    body.innerHTML = (report.results || []).map(item =>
      '<tr>' +
        '<td><span class="badge ' + (item.detected ? "pass" : "fail") + '">' +
          (item.detected ? "命中" : "漏检") + '</span></td>' +
        '<td>' + esc(item.prompt) + '</td>' +
        '<td>' + esc((item.codes || []).join(", ") || "—") + '</td>' +
      '</tr>'
    ).join("") || '<tr><td colspan="3" class="empty">没有结果</td></tr>';
  } catch (error) { message.textContent = error.message; }
}

async function previewRetention() {
  const message = document.getElementById("data-msg");
  const result = document.getElementById("retention-result");
  message.textContent = "加载中…";
  try {
    const data = await api("/api/retention");
    const policy = data.policy || {};
    const preview = data.preview || {};
    result.textContent =
      "保留策略：轨迹 " + policy.trace_days + " 天 · 每轨迹保留 " + policy.report_versions +
      " 个报告版本 · 审计日志 " + policy.audit_days + " 天\\n" +
      "当前预计清理：轨迹 " + (preview.traces_to_purge || 0) + " 条，审计日志 " +
      (preview.audit_events_to_purge || 0) + " 条";
    message.textContent = "预演完成";
  } catch (error) { message.textContent = error.message; }
}

async function exportEvidence() {
  const message = document.getElementById("data-msg");
  const link = document.getElementById("evidence-link");
  message.textContent = "导出中…";
  try {
    const data = await api("/api/evidence/export", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: "{}",
    });
    link.href = data.download_url;
    link.hidden = false;
    link.textContent = "下载证据包（" + data.trace_count + " 条轨迹）→";
    message.textContent = "证据包已生成";
  } catch (error) { message.textContent = error.message; }
}

document.getElementById("sca-run").addEventListener("click", runSca);
document.getElementById("infra-run").addEventListener("click", runInfra);
document.getElementById("redteam-run").addEventListener("click", runRedteam);
document.getElementById("retention-preview").addEventListener("click", previewRetention);
document.getElementById("evidence-export").addEventListener("click", exportEvidence);

// ------------------------- Agent 接入 -------------------------
let selectedAgent = null;
let secretEpoch = 0;
let snippetEpoch = 0;
let modelSettings = null;
let modelDraft = [];
let modelEditing = -1;
let modelDirty = false;
let modelBusy = false;
let modelConflict = false;
let modelEpoch = 0;
const ms = name => document.getElementById('ms-' + name);
function modelMessage(text) { ms('msg').textContent = text; }
function clearModelKey() { ms('key').value = ''; }
function resetModelSettings() {
  modelEpoch += 1;
  clearModelKey();
  modelSettings = null;
  modelDraft = [];
  modelEditing = -1;
  modelDirty = false;
  modelBusy = false;
  modelConflict = false;
  document.getElementById('model-settings').hidden = true;
  ms('editor').hidden = true;
  ms('list').textContent = '';
}
function markModelDirty() {
  modelDirty = true;
  ms('dirty').textContent = '有未保存修改';
  modelMessage('');
  renderModelList();
}
function collectModelForm() {
  if (modelEditing < 0 || !modelDraft[modelEditing] || modelSettings.read_only) return;
  const p = modelDraft[modelEditing];
  p.id = ms('id').value.trim();
  p.display_name = ms('name').value.trim();
  p.provider = ms('provider').value;
  p.base_url = ms('url').value.trim();
  p.model = ms('model').value.trim();
  p.gateway = ms('gateway').checked;
  p.judge = ms('purpose-judge').checked;
  p.supports_stream = ms('stream').checked;
  p.supports_json_object = ms('json').checked;
  p.key_action = ms('clear').checked ? 'clear' : 'keep';
}
function renderModelList() {
  if (!modelSettings) return;
  const ro = modelSettings.read_only || modelBusy;
  ms('list').innerHTML = modelDraft.map((p, i) =>
    '<tr><td>' + esc(p.id || '新连接') + ' / ' + esc(p.display_name) + '</td><td>' +
    esc(p.model) + ' / ' + esc(p.provider) + '</td><td>' + esc(p.key_action === 'clear' ? '待清除' : (p.key_configured ? '已配置' : '未配置')) +
    '</td><td><button data-ms-edit="' + i + '"' + (modelBusy ? ' disabled' : '') + '>编辑</button> ' +
    '<button data-ms-delete="' + i + '"' + (ro || (modelSettings.judge_profile_locked && p.id === modelSettings.overrides.judge_profile) ? ' disabled' : '') + '>删除</button> ' +
    '<button data-ms-test="' + i + '"' + (modelBusy || modelDirty || modelConflict ? ' disabled' : '') + '>测试</button></td></tr>'
  ).join('') || '<tr><td colspan="4" class="empty">暂无连接，点击「新增连接」</td></tr>';
  ms('save').disabled = ro || modelConflict;
  ms('add').disabled = ro || modelConflict;
  ms('reload').disabled = modelBusy;
  ms('editor').disabled = ro;
  ms('test').disabled = ro || modelBusy || modelDirty || modelConflict || modelEditing < 0;
  ms('default').disabled = ro;
  ms('judge').disabled = ro || modelSettings.judge_profile_locked;
  ms('enabled').disabled = ro || modelSettings.judge_enabled_locked;
}
function renderModelSelections() {
  const defaults = ms('default').value;
  const judge = ms('judge').value;
  function options(purpose, selected) {
    const eligible = modelDraft.filter(p => p[purpose]);
    let html = '<option value="">不选择</option>' + eligible.map(p => '<option value="' + esc(p.id) + '">' + esc(p.display_name || p.id) + ' (' + esc(p.id) + ')</option>').join('');
    if (selected && !eligible.some(p => p.id === selected)) html += '<option value="' + esc(selected) + '">' + esc(selected) + '（已失效，请重选或取消）</option>';
    return html;
  }
  ms('default').innerHTML = options('gateway', defaults);
  ms('judge').innerHTML = options('judge', judge);
  ms('default').value = defaults;
  ms('judge').value = judge;
}
function closeModelForm() {
  collectModelForm();
  clearModelKey();
  modelEditing = -1;
  ms('clear').checked = false;
  ms('editor').hidden = true;
  if (modelSettings) { renderModelSelections(); renderModelList(); }
}
function openModelForm(index) {
  if (ms('key').value && !confirm('切换连接将清空尚未提交的 Key，继续？')) return;
  closeModelForm();
  modelEditing = index;
  const p = modelDraft[index];
  for (const [field, value] of Object.entries({id:p.id, name:p.display_name, provider:p.provider, url:p.base_url, model:p.model})) ms(field).value = value || '';
  for (const [field, value] of Object.entries({gateway:p.gateway, 'purpose-judge':p.judge, stream:p.supports_stream, json:p.supports_json_object, clear:p.key_action === 'clear'})) ms(field).checked = !!value;
  ms('id').disabled = modelSettings.profiles.some(saved => saved.id === p.id);
  ms('editor').hidden = false;
}
async function loadModelSettings(force = false) {
  const allowed = can('admin');
  document.getElementById('model-settings').hidden = !allowed;
  document.getElementById('model-readonly-hint').hidden = allowed;
  if (!allowed || (!force && modelSettings)) return;
  const epoch = ++modelEpoch;
  try {
    const data = await api('/api/admin/models');
    if (epoch !== modelEpoch || !can('admin')) return;
    clearModelKey();
    modelSettings = data;
    modelDraft = data.profiles.map(p => Object.assign({}, p, {key_action:'keep'}));
    modelEditing = -1;
    modelDirty = false;
    modelConflict = false;
    ms('editor').hidden = true;
    ms('source').textContent = (data.read_only ? data.read_only_reason : '本机可编辑') +
      ' · revision ' + data.revision +
      (data.effective_judge_enabled ? ' · 裁判已开启' : ' · 裁判未开启') +
      (data.judge_profile_locked ? ' · 裁判连接锁定' : '') +
      (data.judge_enabled_locked ? ' · 裁判开关锁定' : '');
    ms('default').innerHTML = '';
    ms('judge').innerHTML = '';
    renderModelSelections();
    ms('default').value = data.default_gateway || '';
    ms('judge').value = data.judge_profile || '';
    ms('enabled').checked = data.judge_enabled;
    ms('dirty').textContent = '已保存';
    modelMessage('');
    renderModelList();
    if (modelDraft.length) {
      const preferred = data.default_gateway || data.judge_profile;
      let index = modelDraft.findIndex(p => p.id === preferred);
      openModelForm(index < 0 ? 0 : index);
    }
    loadJudgePrompt();
  } catch (error) {
    if (epoch === modelEpoch) modelMessage('设置加载失败，请重新加载。');
  }
}
let judgePromptLoaded = false;
async function loadJudgePrompt() {
  if (judgePromptLoaded) return;
  try {
    const data = await api('/api/judge/prompt');
    judgePromptLoaded = true;
    document.getElementById('judge-prompt-text').textContent = data.prompt || '';
    document.getElementById('judge-prompt-meta').textContent =
      '温度 ' + data.temperature + ' · 输出格式 ' + data.response_format +
      ' · 最多重试 ' + data.max_retries + ' 次';
    document.getElementById('judge-prompt-guards').textContent =
      '约束：' + (data.guards || []).join('；');
  } catch (error) {
    document.getElementById('judge-prompt-text').textContent = '加载失败：' + error.message;
  }
}
// ------------------------- 系统设置：参数 / 检索 / 数据 -------------------------
let auditParamsState = null;

function setSystemMessage(id, text) {
  const node = document.getElementById(id);
  if (node) node.textContent = text;
}

function applySysTabs() {
  document.querySelectorAll(".sys-switch-btn").forEach(btn =>
    btn.classList.toggle("active", btn.dataset.sys === sysSection));
  const configPane = document.getElementById("sys-pane-config");
  const dataPane = document.getElementById("sys-pane-data");
  if (configPane) configPane.hidden = sysSection !== "config";
  if (dataPane) dataPane.hidden = sysSection !== "data";
  document.querySelectorAll(".sys-subtab").forEach(btn =>
    btn.classList.toggle("active", btn.dataset.sysSub === sysConfigTab));
  document.querySelectorAll(".sys-subpane").forEach(pane =>
    pane.classList.toggle("active", pane.id === "sys-sub-" + sysConfigTab));
  if (sysSection === "data") loadDataStats();
}

async function loadAuditParams() {
  try {
    const data = await api("/api/admin/audit-params");
    auditParamsState = data;
    const params = data.params || {};
    const fill = (id, value) => { const el = document.getElementById(id); if (el) el.value = value; };
    fill("ap-timeout", params.timeout_s);
    fill("ap-retries", params.max_retries);
    fill("ap-tokens", params.max_tokens);
    fill("ap-chars", params.max_input_chars);
    fill("ap-kb", params.kb_limit);
    setSystemMessage("ap-msg", "");
    const limit = document.getElementById("kb-limit");
    if (limit) limit.textContent = Number(params.kb_limit || 0);
  } catch (error) {
    setSystemMessage("ap-msg", error.status === 403 ? "需要管理员权限" : "参数加载失败");
  }
}

async function saveAuditParams() {
  const button = document.getElementById("ap-save");
  const params = {
    timeout_s: Number(document.getElementById("ap-timeout").value),
    max_retries: Number(document.getElementById("ap-retries").value),
    max_tokens: Number(document.getElementById("ap-tokens").value),
    max_input_chars: Number(document.getElementById("ap-chars").value),
    kb_limit: Number(document.getElementById("ap-kb").value)
  };
  button.disabled = true;
  setSystemMessage("ap-msg", "保存中…");
  try {
    const data = await api("/api/admin/audit-params", {
      method: "PUT",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({params: params})
    });
    auditParamsState = data;
    setSystemMessage("ap-msg", "已保存，下一次审计生效");
    const limit = document.getElementById("kb-limit");
    if (limit) limit.textContent = Number(data.params.kb_limit || 0);
  } catch (error) {
    setSystemMessage("ap-msg", error.message || "保存失败");
  } finally {
    button.disabled = false;
  }
}

async function loadKbStatus() {
  try {
    const data = await api("/api/admin/kb");
    const put = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
    put("kb-documents", Number(data.documents || 0));
    put("kb-chunks", Number(data.chunks || 0));
    put("kb-tokenizer", data.tokenizer || "—");
    const params = (auditParamsState && auditParamsState.params) || {};
    put("kb-limit", Number(params.kb_limit || 0) || "—");
    setSystemMessage("kb-msg", data.ready ? "" : "索引尚未就绪");
  } catch (error) {
    setSystemMessage("kb-msg", error.status === 403 ? "需要管理员权限" : "知识库状态加载失败");
  }
}

async function rebuildKb() {
  const button = document.getElementById("kb-rebuild");
  button.disabled = true;
  setSystemMessage("kb-msg", "重建中…");
  try {
    const data = await api("/api/admin/kb/rebuild", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: "{}"
    });
    setSystemMessage("kb-msg", "已重建 " + Number(data.rebuilt || data.chunks || 0) + " 个切片");
    await loadKbStatus();
  } catch (error) {
    setSystemMessage("kb-msg", error.message || "重建失败");
  } finally {
    button.disabled = false;
  }
}

function bytesText(value) {
  const size = Number(value) || 0;
  if (size < 1024) return size + " B";
  if (size < 1024 * 1024) return (size / 1024).toFixed(1) + " KB";
  return (size / 1024 / 1024).toFixed(2) + " MB";
}

async function loadDataStats() {
  const put = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
  setSystemMessage("dm-msg", "加载中…");
  try {
    const data = await api("/api/admin/data-stats");
    put("dm-db", data.db_ok ? "正常" : "异常");
    put("dm-size", bytesText(data.db_size_bytes));
    put("dm-traces", Number(data.traces || 0));
    put("dm-agents", Number(data.agents || 0));
    put("dm-approvals", Number(data.approvals_pending || 0));
    put("dm-policies", Number(data.policies || 0));
    put("dm-events", Number(data.audit_events || 0));
    setSystemMessage("dm-msg", "已刷新");
  } catch (error) {
    setSystemMessage("dm-msg", error.status === 403 ? "需要管理员权限" : (error.message || "统计加载失败"));
  }
}

const systemPanel = document.getElementById("panel-settings");
if (systemPanel) {
  systemPanel.addEventListener("click", event => {
    const main = event.target.closest("[data-sys]");
    if (main) { sysSection = main.dataset.sys; applySysTabs(); return; }
    const sub = event.target.closest("[data-sys-sub]");
    if (sub) { sysConfigTab = sub.dataset.sysSub; applySysTabs(); }
  });
}
document.getElementById("ap-save").addEventListener("click", saveAuditParams);
document.getElementById("ap-reload").addEventListener("click", loadAuditParams);
document.getElementById("kb-refresh").addEventListener("click", loadKbStatus);
document.getElementById("kb-rebuild").addEventListener("click", rebuildKb);
document.getElementById("dm-refresh").addEventListener("click", loadDataStats);
applySysTabs();

function modelOperationError(error) {
  if (error.status === 409) {
    modelConflict = true;
    modelMessage('设置已被其他管理员修改。请重新加载后重新编辑；不会自动覆盖。');
  } else modelMessage('操作失败，请检查连接配置、密钥操作和权限后重试。未显示原始服务响应。');
}
async function saveModelSettings() {
  if (!modelSettings || modelSettings.read_only || modelBusy || modelConflict) return;
  collectModelForm();
  renderModelSelections();
  const profiles = modelDraft.map((p, i) => {
    const item = {};
    for (const field of ['id','display_name','provider','base_url','model','gateway','judge','supports_stream','supports_json_object']) item[field] = p[field];
    item.key_action = p.key_action || 'keep';
    if (i === modelEditing && !ms('clear').checked && ms('key').value) {
      item.key_action = 'replace';
      item.api_key = ms('key').value;
    }
    return item;
  });
  const payload = {expected_revision:modelSettings.revision, profiles, default_gateway:ms('default').value || null,
    judge_profile:ms('judge').value || null, judge_enabled:ms('enabled').checked};
  const epoch = modelEpoch;
  modelBusy = true;
  renderModelList();
  try {
    await api('/api/admin/models', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    if (epoch !== modelEpoch || !can('admin')) return;
    clearModelKey();
    await loadModelSettings(true);
    await loadModels();
    if (can('admin')) modelMessage('已保存并生效；尚未验证真实联通，原测试状态已失效。');
  } catch (error) {
    if (epoch === modelEpoch) modelOperationError(error);
  } finally {
    if (epoch === modelEpoch) clearModelKey();
    profiles.forEach(p => { delete p.api_key; });
    if (epoch === modelEpoch || can('admin')) { modelBusy = false; renderModelList(); }
  }
}
async function testModelConnection(index) {
  if (!modelSettings || modelBusy || modelDirty || modelConflict) return;
  if (!confirm('将向该连接发送一次真实请求，可能产生少量费用；是否继续？')) return;
  const epoch = modelEpoch;
  modelBusy = true;
  renderModelList();
  try {
    const result = await api('/api/admin/models/test', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id:modelDraft[index].id, expected_revision:modelSettings.revision})});
    if (epoch !== modelEpoch || !can('admin')) return;
    const categories = {ok:'成功', missing_key:'未配置密钥', invalid_response:'响应格式不符', upstream_error:'上游拒绝', response_too_large:'响应超限', timeout:'请求超时', network_error:'网络或出站校验失败'};
    modelMessage('连接测试：' + (categories[result.category] || '未知') + ' · ' + (Number(result.elapsed_ms) || 0) + ' ms');
  } catch (error) { if (epoch === modelEpoch) modelOperationError(error); }
  finally { if (epoch === modelEpoch) { modelBusy = false; renderModelList(); } }
}
ms('editor').addEventListener('input', event => {
  if (modelBusy || !modelSettings || modelSettings.read_only) return;
  if (event.target === ms('key') && ms('key').value) ms('clear').checked = false;
  collectModelForm();
  renderModelSelections();
  markModelDirty();
});
ms('provider').addEventListener('change', () => {
  const urls = {openai:'https://api.openai.com/v1', deepseek:'https://api.deepseek.com', dashscope:'https://dashscope.aliyuncs.com/compatible-mode/v1'};
  if (urls[ms('provider').value]) ms('url').value = urls[ms('provider').value];
  collectModelForm();
});
ms('clear').addEventListener('change', () => { if (ms('clear').checked) clearModelKey(); collectModelForm(); markModelDirty(); });
for (const name of ['default','judge','enabled']) ms(name).addEventListener('change', markModelDirty);
ms('close').addEventListener('click', closeModelForm);
ms('save').addEventListener('click', saveModelSettings);
ms('test').addEventListener('click', () => { if (modelEditing >= 0) testModelConnection(modelEditing); });
ms('reload').addEventListener('click', () => {
  if ((modelDirty || ms('key').value) && !confirm('重新加载将丢弃所有未保存修改及 Key，继续？')) return;
  clearModelKey();
  loadModelSettings(true);
});
ms('add').addEventListener('click', () => {
  if (!modelSettings || modelSettings.read_only || modelBusy) return;
  if (ms('key').value && !confirm('新增将清空当前待提交 Key，继续？')) return;
  closeModelForm();
  modelDraft.push({id:'', display_name:'', provider:'openai', base_url:'https://api.openai.com/v1', model:'', gateway:true, judge:false, supports_stream:true, supports_json_object:true, key_configured:false, key_action:'keep'});
  markModelDirty();
  openModelForm(modelDraft.length - 1);
});
ms('list').addEventListener('click', event => {
  const button = event.target.closest('button');
  if (!button || button.disabled || modelBusy) return;
  if (button.dataset.msEdit !== undefined) openModelForm(Number(button.dataset.msEdit));
  if (button.dataset.msTest !== undefined) testModelConnection(Number(button.dataset.msTest));
  if (button.dataset.msDelete !== undefined) {
    const index = Number(button.dataset.msDelete);
    const p = modelDraft[index];
    if (modelSettings.read_only || (modelSettings.judge_profile_locked && p.id === modelSettings.overrides.judge_profile)) return;
    if (!confirm('删除连接？若它是默认网关或裁判，请在保存前重选或取消。待提交 Key 将清空。')) return;
    closeModelForm();
    modelDraft.splice(index, 1);
    renderModelSelections();
    markModelDirty();
  }
});
window.addEventListener('beforeunload', event => { if (modelDirty) { event.preventDefault(); event.returnValue = ''; } });
function updateAgentModelField() {
  document.getElementById("agent-model-field").hidden = document.getElementById("agent-framework").value !== "openai";
}
async function loadModels() {
  const select = document.getElementById("agent-model");
  const previous = select.value;
  const wasDisabled = select.disabled;
  try {
    const data = await api("/api/models");
    const models = data.models || [];
    const mock = data.mock === true;
    const available = mock ? [{id: "mock-model"}] : models.filter(model => model.gateway && model.key_configured);
    select.innerHTML = available.length ? available.map(model =>
      '<option value="' + esc(model.id) + '">' + esc(model.id) + (mock ? '（模拟模式）' : '') + '</option>'
    ).join("") : '<option value="">无可用网关模型</option>';
    select.disabled = !available.length;
    if (available.some(model => model.id === previous)) select.value = previous;
  } catch (error) {
    select.innerHTML = '<option value="">模型目录不可用</option>';
    select.disabled = true;
  }
  if (selectedAgent && document.getElementById("agent-framework").value === "openai" &&
      (previous !== select.value || wasDisabled !== select.disabled)) {
    loadAgentSnippet().catch(error => agentMessage(error.message));
  }
}
function clearAgentSecret() {
  secretEpoch += 1;
  document.getElementById("agent-secret").value = "";
  document.getElementById("agent-secret-box").hidden = true;
}
function agentPath(id) { return "/api/agents/" + encodeURIComponent(id); }

async function loadAgentVersions() {
  const message = document.getElementById("v-msg");
  const agent = document.getElementById("v-agent").value.trim();
  const body = document.getElementById("agent-versions");
  if (!agent) {
    message.textContent = "请先填写智能体 ID";
    return;
  }
  message.textContent = "加载中…";
  try {
    const data = await api(agentPath(agent) + "/versions");
    const versions = data.versions || [];
    if (!versions.length) {
      body.innerHTML = '<tr><td colspan="7" class="empty">该智能体还没有记录</td></tr>';
      message.textContent = "无数据";
      return;
    }
    body.innerHTML = versions.map(item => {
      const rate = item.traces ? (item.risk_traces / item.traces * 100) : 0;
      return '<tr>' +
        '<td><code>' + esc(item.version) + '</code></td>' +
        '<td>' + Number(item.traces || 0) + '</td>' +
        '<td>' + Number(item.risk_traces || 0) + '</td>' +
        '<td>' + rate.toFixed(1) + '%</td>' +
        '<td>' + money(item.cost_usd) + '</td>' +
        '<td>' + Number(item.avg_hits || 0) + '</td>' +
        '<td class="muted">' + esc(timeText(item.latest_at)) + '</td>' +
      '</tr>';
    }).join("");
    message.textContent = "共 " + versions.length + " 个版本";
  } catch (error) {
    body.innerHTML = '<tr><td colspan="7" class="empty">' + esc(error.message) + '</td></tr>';
    message.textContent = "加载失败";
  }
}
document.getElementById("v-load").addEventListener("click", loadAgentVersions);
function agentMessage(text) { document.getElementById("agent-msg").textContent = text; }
async function loadAgents() {
  await loadModels();
  await loadModelSettings();
  document.getElementById("system-status-card").hidden = !can("admin");
  document.getElementById("agent-register-form").hidden = !can("admin");
  document.getElementById("agent-register").disabled = !can("admin");
  document.getElementById("agent-key-admin").hidden = !can("admin");
  try {
    const data = await api("/api/agents");
    document.getElementById("agents").innerHTML = (data.agents || []).map(a =>
      '<tr><td><code>' + esc(a.agent_id) + '</code><div>' + esc(a.name) + '</div></td>' +
      '<td>' + (a.enabled ? '启用' : '禁用') + '</td><td>' + esc(a.last_seen || '尚未认证') + '</td>' +
      '<td>' + Number(a.received_count || 0) + '</td><td>' + Number(a.report_count || 0) + '</td>' +
      '<td><button data-agent-open="' + esc(a.agent_id) + '">接入配置</button> ' +
      '<button data-agent-toggle="' + esc(a.agent_id) + '" data-enabled="' + (a.enabled ? '0' : '1') + '"' +
      (can("admin") ? '' : ' disabled') + '>' + (a.enabled ? '禁用' : '启用') + '</button> ' +
      '<button data-agent-delete="' + esc(a.agent_id) + '"' + (can("admin") ? '' : ' disabled') + '>删除</button></td></tr>'
    ).join("") || '<tr><td colspan="6" class="empty">尚未注册智能体</td></tr>';
  } catch (error) { agentMessage(error.message); }
}
function uptimeText(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return (days ? days + " 天 " : "") + (hours ? hours + " 小时 " : "") + minutes + " 分钟";
}
async function loadSystemStatus() {
  const body = document.getElementById("system-status");
  const message = document.getElementById("system-status-msg");
  if (!can("admin")) { message.textContent = "需要管理员权限"; return; }
  message.textContent = "加载中…";
  try {
    const data = await api("/api/system/status");
    const modeSelect = document.getElementById("global-mode");
    const modeButton = document.getElementById("global-mode-save");
    const modeMsg = document.getElementById("global-mode-msg");
    modeSelect.value = data.mode === "enforce" ? "enforce" : "shadow";
    modeSelect.disabled = Boolean(data.mode_locked);
    modeButton.disabled = Boolean(data.mode_locked);
    if (data.mode_locked) {
      modeMsg.textContent = "当前由启动参数锁定；去掉 --mode / SAFEORBIT_MODE 后可在网页切换";
    } else if (data.mode_source === "admin") {
      modeMsg.textContent = "已由管理员在网页设置，重启后仍生效";
    } else {
      modeMsg.textContent = "当前为默认值；切换后立即生效并持久化";
    }
    const rows = [
      ["版本", data.version, "当前运行版本"],
      ["运行模式", data.mode === "shadow" ? "影子模式" : "拦截模式", "enforce 才会阻断调用"],
      ["运行时长", uptimeText(data.uptime_seconds), "本次进程启动至今"],
      ["数据库", data.db_ok ? "正常" : "异常", data.db_ok ? "读写可用" : "检查 SQLite 路径与磁盘权限"],
      ["语义裁判", data.judge_enabled ? "已启用" : "未启用", data.judge_enabled ? "异步执行，失败自动降级" : "仅规则审计"],
      ["行为事件", String(Number((data.events || {}).events || 0)), "已入库的规范化事件"],
    ];
    const circuits = data.circuits || {};
    const names = Object.keys(circuits).sort();
    if (!names.length) rows.push(["熔断器", "无记录", "尚未触发出站调用"]);
    names.forEach(name => {
      const item = circuits[name] || {};
      const state = item.state === "open" ? "已打开" : (item.state === "half_open" ? "半开探测" : "正常");
      rows.push(["熔断 · " + name, state, "连续失败 " + Number(item.failures || 0) + " 次"]);
    });
    const counters = data.counters || {};
    Object.keys(counters).sort().forEach(name => rows.push(["计数 · " + name, String(counters[name]), "进程内累计"]));
    body.innerHTML = rows.map(row =>
      '<tr><td>' + esc(row[0]) + '</td><td>' + esc(String(row[1])) + '</td><td class="muted">' + esc(row[2]) + '</td></tr>'
    ).join("");
    message.textContent = "已刷新";
  } catch (error) {
    message.textContent = error.message;
    body.innerHTML = '<tr><td colspan="3" class="empty">无法读取运行状态（需要管理员会话）</td></tr>';
  }
}
document.getElementById("system-status-refresh").addEventListener("click", loadSystemStatus);
document.getElementById("global-mode-save").addEventListener("click", async () => {
  const select = document.getElementById("global-mode");
  const button = document.getElementById("global-mode-save");
  const message = document.getElementById("global-mode-msg");
  const mode = select.value;
  if (mode === "enforce" && !window.confirm(
    "切换到拦截模式后，已设为「允许阻断」的智能体将真正拒绝危险调用。确认切换？"
  )) return;
  button.disabled = true;
  message.textContent = "切换中…";
  try {
    const data = await api("/api/system/mode", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({mode: mode})
    });
    message.textContent = "已切换为" + (data.mode === "enforce" ? "拦截模式" : "影子模式");
    await loadSystemStatus();
    refreshTraces();
  } catch (error) {
    message.textContent = error.message;
    button.disabled = false;
  }
});
async function loadAgentKeys() {
  if (!can("admin") || !selectedAgent) return;
  const id = selectedAgent;
  const data = await api(agentPath(id) + "/keys");
  if (id !== selectedAgent || !can("admin")) return;
  document.getElementById("agent-keys").innerHTML = (data.keys || []).map(k =>
    '<tr><td><code>' + esc(k.prefix) + '</code></td><td>' + esc((k.scopes || []).join(', ')) + '</td>' +
    '<td>' + esc(k.expires_at || '不过期') + '</td><td>' + (k.revoked ? '已撤销' : (k.expires_at && Date.parse(k.expires_at) <= Date.now() ? '已过期' : '有效')) + '</td>' +
    '<td><button data-key-revoke="' + esc(k.key_id) + '"' + (k.revoked ? ' disabled' : '') + '>撤销</button></td></tr>'
  ).join("") || '<tr><td colspan="5" class="empty">尚未签发 Key</td></tr>';
}
async function loadAgentSnippet() {
  const epoch = ++snippetEpoch;
  const output = document.getElementById("agent-snippet");
  output.textContent = "";
  updateAgentModelField();
  if (!selectedAgent) return;
  const id = selectedAgent;
  const framework = document.getElementById("agent-framework").value;
  const select = document.getElementById("agent-model");
  const model = select.value;
  if (framework === "openai" && (select.disabled || !model)) {
    output.textContent = "暂无可用模型，无法生成 OpenAI SDK 示例。请检查模型目录、本机密钥配置或模拟模式。";
    document.getElementById("agent-snippet-copy").disabled = true;
    return;
  }
  document.getElementById("agent-snippet-copy").disabled = true;
  const query = "?framework=" + encodeURIComponent(framework) +
    (framework === "openai" ? "&model=" + encodeURIComponent(model) : "");
  const data = await api(agentPath(id) + "/snippet" + query);
  if (epoch === snippetEpoch && id === selectedAgent &&
      framework === document.getElementById("agent-framework").value &&
      (framework !== "openai" || model === select.value)) {
    output.textContent = data.snippet;
    document.getElementById("agent-snippet-copy").disabled = false;
  }
}
async function selectAgent(id) {
  clearAgentSecret();
  selectedAgent = id;
  document.getElementById("agent-selected").textContent = id;
  document.getElementById("agent-detail").hidden = false;
  document.getElementById("agent-keys").textContent = "";
  document.getElementById("agent-snippet").textContent = "";
  document.getElementById("agent-key-admin").hidden = !can("admin");
  try { await Promise.all([loadAgentKeys(), loadAgentSnippet()]); }
  catch (error) { agentMessage(error.message); }
}
async function registerAgent() {
  if (!can("admin")) return;
  try {
    const data = await api("/api/agents", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({agent_id: document.getElementById("agent-id").value.trim(), name: document.getElementById("agent-name").value.trim()})});
    agentMessage("已注册");
    await loadAgents();
    await selectAgent(data.agent.agent_id);
  } catch (error) { agentMessage(error.message); }
}
async function issueAgentKey() {
  if (!can("admin") || !selectedAgent) return;
  clearAgentSecret();
  const epoch = secretEpoch;
  const id = selectedAgent;
  const button = document.getElementById("agent-issue");
  button.disabled = true;
  try {
    const expiry = document.getElementById("agent-expiry").value;
    const data = await api(agentPath(id) + "/keys", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({scopes: Array.from(document.querySelectorAll("#agent-scopes input:checked")).map(input => input.value),
        expires_at: expiry ? new Date(expiry).toISOString() : null})});
    if (epoch === secretEpoch && id === selectedAgent && can("admin")) {
      document.getElementById("agent-secret").value = data.key;
      document.getElementById("agent-secret-box").hidden = false;
      agentMessage("Key 仅展示一次，请安全保存后清除");
      await loadAgentKeys();
    }
  } catch (error) { agentMessage(error.message); }
  finally { button.disabled = false; }
}
async function copyAgentText(id) {
  try {
    const node = document.getElementById(id);
    const text = id === "agent-secret" ? node.value : node.textContent;
    if (!text) return;
    await navigator.clipboard.writeText(text);
    agentMessage("已复制；系统剪贴板不会随页面清除，请妥善处理");
  } catch (error) { agentMessage("复制失败，请在 HTTPS 或本机页面手动复制"); }
}
document.getElementById("agent-register").addEventListener("click", registerAgent);
document.getElementById("agent-refresh").addEventListener("click", async () => {
  await loadAgents();
  try { await loadAgentKeys(); } catch (error) { agentMessage(error.message); }
});
document.getElementById("agent-issue").addEventListener("click", issueAgentKey);
document.getElementById("agent-secret-clear").addEventListener("click", clearAgentSecret);
document.getElementById("agent-secret-copy").addEventListener("click", () => copyAgentText("agent-secret"));
document.getElementById("agent-snippet-copy").addEventListener("click", () => copyAgentText("agent-snippet"));
document.getElementById("agent-framework").addEventListener("change", () => {
  loadAgentSnippet().catch(error => agentMessage(error.message));
});
document.getElementById("agent-model").addEventListener("change", () => {
  loadAgentSnippet().catch(error => agentMessage(error.message));
});
document.getElementById("agents").addEventListener("click", async event => {
  const open = event.target.closest("button[data-agent-open]");
  if (open) { await selectAgent(open.dataset.agentOpen); return; }
  const toggle = event.target.closest("button[data-agent-toggle]");
  if (toggle) {
    if (!can("admin")) return;
    clearAgentSecret();
    try {
      await api(agentPath(toggle.dataset.agentToggle), {method: "PATCH", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({enabled: toggle.dataset.enabled === "1"})});
      await loadAgents();
    } catch (error) { agentMessage(error.message); }
    return;
  }
  const remove = event.target.closest("button[data-agent-delete]");
  if (!remove || !can("admin")) return;
  const agentId = remove.dataset.agentDelete;
  if (!window.confirm("删除智能体 " + agentId + "？其 Key 会立即失效，历史轨迹与报告保留。")) return;
  clearAgentSecret();
  try {
    await api(agentPath(agentId), {method: "DELETE"});
    agentMessage("已删除 " + agentId);
    if (selectedAgent === agentId) {
      selectedAgent = null;
      document.getElementById("agent-detail").hidden = true;
      document.getElementById("agent-keys").textContent = "";
      document.getElementById("agent-snippet").textContent = "";
    }
    if (navState.agentId === agentId) {
      navState.agentId = null;
      if (navState.module === "agent" || navState.module === "task") setModule("agents");
    }
    await loadAgents();
    await refreshNav(true);
  } catch (error) { agentMessage(error.message); }
});
document.getElementById("agent-keys").addEventListener("click", async event => {
  const revoke = event.target.closest("button[data-key-revoke]");
  if (!revoke || !can("admin") || !selectedAgent) return;
  if (!window.confirm("确认撤销此 Key？旧 Key 将立即失效。")) return;
  clearAgentSecret();
  try {
    await api(agentPath(selectedAgent) + "/keys/" + encodeURIComponent(revoke.dataset.keyRevoke), {method: "DELETE"});
    await loadAgentKeys();
  } catch (error) { agentMessage(error.message); }
});
window.addEventListener("pagehide", clearAgentSecret);

// ------------------------- 单侧边栏导航（固定模块 / 任务组 / 身份） -------------------------
const NAV_KEY = "safeorbit.nav.v2";
const TASK_KEY = "safeorbit.tasks.v1";
const MODULE_META = {
  guide: {label: "使用导航", panel: "guide"},
  overview: {label: "总览", panel: "overview"},
  runs: {label: "运行记录", panel: "traces"},
  audit: {label: "审计视图", panel: "audit"},
  approvals: {label: "审批队列", panel: "dispositions"},
  policies: {label: "策略配置", panel: "policies"},
  codeaudit: {label: "代码审计", panel: "codeaudit"},
  redteam: {label: "红队检测", panel: "redteam"},
  agents: {label: "Agent 管理", panel: "agents"},
  models: {label: "系统设置", panel: "settings"},
  settings: {label: "系统设置", panel: "settings"},
  agent: {label: "Agent 工作台", panel: "agent"},
  task: {label: "任务详情", panel: "task"},
  account: {label: "设置", panel: "account"},
};
const GUIDE_MODULES = ["guide", "overview", "runs", "approvals", "policies", "codeaudit", "redteam"];
let navRuns = [];
let navApprovals = [];
let navPolicies = [];
let navAgents = [];
let navTasks = [];
let navState = {
  module: "guide", panel: "guide", view: "timeline", runId: null,
  agentId: null, taskId: null, agentTab: "total",
  groups: {agents: true, agent_audit: true, code_audit: true, redteam: true},
  width: 252, collapsed: false,
};
let navLoadedAt = 0;
let overlayOpen = false;

function narrowMode() { return window.matchMedia("(max-width: 1023px)").matches; }

function loadNavState() {
  try {
    const raw = JSON.parse(localStorage.getItem(NAV_KEY) || "null");
    if (!raw || typeof raw !== "object") return;
    if (MODULE_META[raw.module]) {
      navState.module = raw.module;
      navState.panel = MODULE_META[raw.module].panel;
    }
    if (["timeline", "table", "agent"].includes(raw.view)) navState.view = raw.view;
    if (["total", "records"].includes(raw.agentTab)) navState.agentTab = raw.agentTab;
    if (typeof raw.runId === "string" && raw.runId) navState.runId = raw.runId;
    if (typeof raw.agentId === "string" && raw.agentId) navState.agentId = raw.agentId;
    if (typeof raw.taskId === "string" && raw.taskId) navState.taskId = raw.taskId;
    if (raw.groups && typeof raw.groups === "object") {
      Object.keys(navState.groups).forEach(key => {
        if (typeof raw.groups[key] === "boolean") navState.groups[key] = raw.groups[key];
      });
    }
    navState.width = Math.min(360, Math.max(232, Number(raw.width) || 252));
    navState.collapsed = !!raw.collapsed;
  } catch (error) {}
}

function persistNav() {
  try {
    localStorage.setItem(NAV_KEY, JSON.stringify({
      module: navState.module, view: navState.view, runId: navState.runId,
      agentId: navState.agentId, taskId: navState.taskId, agentTab: navState.agentTab,
      groups: navState.groups,
      width: navState.width, collapsed: navState.collapsed,
    }));
  } catch (error) {}
}

function applyResponsive() {
  const app = document.getElementById("app");
  const side = document.getElementById("side");
  const scrim = document.getElementById("side-scrim");
  if (!app || !side) return;
  if (narrowMode()) {
    app.style.setProperty("--sidew", "0px");
    side.classList.remove("collapsed");
    side.classList.toggle("overlay-open", overlayOpen);
    if (scrim) scrim.hidden = !overlayOpen;
  } else {
    overlayOpen = false;
    app.style.setProperty("--sidew", navState.collapsed ? "0px" : navState.width + "px");
    side.classList.toggle("collapsed", navState.collapsed);
    if (scrim) scrim.hidden = true;
  }
  const toggle = document.getElementById("side-collapse");
  if (toggle) toggle.textContent = narrowMode() ? "×" : (navState.collapsed ? "»" : "«");
}

function toggleSide() {
  if (narrowMode()) {
    overlayOpen = !overlayOpen;
  } else {
    navState.collapsed = !navState.collapsed;
    overlayOpen = false;
  }
  persistNav();
  applyResponsive();
}

function closeSideOverlay() {
  if (!narrowMode()) return;
  overlayOpen = false;
  applyResponsive();
}

function syncModuleActive() {
  const active = navState.module;
  document.querySelectorAll("[data-module]").forEach(node => {
    const on = node.dataset.module === active;
    node.classList.toggle("active", on);
    if (node.tagName === "BUTTON") node.setAttribute("aria-current", on ? "page" : "false");
  });
  const guide = document.getElementById("side-guide-toggle");
  if (guide) guide.classList.toggle("active", GUIDE_MODULES.includes(active));
  document.querySelectorAll("[data-agent-row]").forEach(node =>
    node.classList.toggle("active", active === "agent" && node.dataset.agentRow === navState.agentId));
  document.querySelectorAll("[data-task-row]").forEach(node =>
    node.classList.toggle("active", active === "task" && node.dataset.taskRow === navState.taskId));
  const user = document.getElementById("side-user");
  if (user) user.classList.toggle("active", active === "account");
}

function verdictColor(verdict) {
  if (verdict === "fail") return "var(--red)";
  if (verdict === "warn") return "var(--amber)";
  if (verdict === "review") return "#9dc0e6";
  return "var(--green)";
}

function relTime(value) {
  if (!value) return "";
  const diff = Date.now() - Date.parse(value);
  if (Number.isNaN(diff)) return "";
  const seconds = Math.max(0, Math.floor(diff / 1000));
  if (seconds < 60) return seconds + "s";
  if (seconds < 3600) return Math.floor(seconds / 60) + "m";
  if (seconds < 86400) return Math.floor(seconds / 3600) + "h";
  return Math.floor(seconds / 86400) + "d";
}

function dayLabel(value) {
  if (!value) return "未知时间";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未知时间";
  const now = new Date();
  const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  if (sameDay(date, now)) return "今天";
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(date, yesterday)) return "昨天";
  return (date.getMonth() + 1) + " 月 " + date.getDate() + " 日";
}

function pendingByTrace() {
  const map = {};
  navApprovals.forEach(item => {
    if (item.status === "proposed" && item.trace_id) map[item.trace_id] = (map[item.trace_id] || 0) + 1;
  });
  return map;
}

// ------------------------- 任务（本地持久化） -------------------------
const TASK_TYPES = {
  agent_audit: {label: "Agent 审计", group: "Agent 审计"},
  code_audit: {label: "代码审计", group: "代码审计"},
  redteam: {label: "红队检测", group: "红队检测"},
};

function loadTasks() {
  try {
    const raw = JSON.parse(localStorage.getItem(TASK_KEY) || "[]");
    navTasks = Array.isArray(raw)
      ? raw.filter(item => item && item.id && TASK_TYPES[item.type])
      : [];
  } catch (error) { navTasks = []; }
}

function saveTasks() {
  try { localStorage.setItem(TASK_KEY, JSON.stringify(navTasks.slice(0, 120))); } catch (error) {}
}

function newTaskId(prefix) {
  return prefix + "-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 6);
}

function addTask(task) {
  navTasks.unshift(task);
  saveTasks();
  renderSide();
  return task;
}

function patchTask(id, patch) {
  const task = findTask(id);
  if (!task) return;
  Object.assign(task, patch);
  saveTasks();
  renderSide();
}

function findTask(id) { return navTasks.find(item => item.id === id) || null; }

function taskStatusColor(status) {
  if (status === "running") return "var(--amber)";
  if (status === "failed") return "var(--red)";
  return "var(--green)";
}

function agentRuns(agentId) {
  return navRuns.filter(run => run.agent_id === agentId);
}

function agentTaskCount(agentId) {
  return navTasks.filter(item => item.agent_id === agentId).length;
}

function pendingAgentCount(agentId) {
  return navApprovals.filter(item => item.status === "proposed" && item.agent_id === agentId).length;
}

function renderSideTasks(type) {
  const items = navTasks.filter(item => item.type === type).slice(0, 6);
  if (!items.length) return '<div class="side-empty">还没有任务</div>';
  return items.map(item =>
    '<button type="button" class="side-item' + (navState.module === "task" && navState.taskId === item.id ? " active" : "") +
      '" data-task-row="' + esc(item.id) + '">' +
      '<span class="dot" style="background:' + taskStatusColor(item.status) + '"></span>' +
      '<span class="name">' + esc(item.title) + '</span>' +
      '<span class="meta">' + (item.status === "running" ? "进行中" : esc(relTime(item.created_at))) + '</span>' +
    '</button>').join("");
}

const GROUP_ICONS = {
  agents: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 11.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4z"/><path d="M3.6 19.4c0-3 2.4-5 5.4-5s5.4 2 5.4 5"/><path d="M16 5.9a3 3 0 0 1 0 5.6"/><path d="M17.4 14.9c2 .6 3.1 2.2 3.1 4.1"/></svg>',
  agent_audit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5.5" y="4.6" width="13" height="15.9" rx="2"/><path d="M9 4.6V3.2h6v1.4"/><path d="m9 12.6 2 2 4-4"/></svg>',
  code_audit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m8.8 8.2-4 3.8 4 3.8"/><path d="m15.2 8.2 4 3.8-4 3.8"/></svg>',
  redteam: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="7.6"/><circle cx="12" cy="12" r="3"/><path d="M12 2.4v2.6M12 19v2.6M2.4 12h2.6M19 12h2.6"/></svg>',
};

function renderSide() {
  const groups = document.getElementById("side-groups");
  if (!groups) return;
  const agentRows = navAgents.length
    ? navAgents.map(agent => {
        const runs = agentRuns(agent.agent_id);
        const waiting = pendingAgentCount(agent.agent_id);
        return '<button type="button" class="side-item' +
          (navState.module === "agent" && navState.agentId === agent.agent_id ? " active" : "") +
          '" data-agent-row="' + esc(agent.agent_id) + '" title="' + esc(agent.name || agent.agent_id) + '">' +
          '<span class="dot" style="background:' + (agent.enabled ? "var(--green)" : "#5f656d") + '"></span>' +
          '<span class="name">' + esc(agent.name || agent.agent_id) + '</span>' +
          '<span class="meta">' + (waiting ? '<b class="hot">' + waiting + ' 待审</b>' : runs.length + " 运行") +
          '</span></button>';
      }).join("")
    : '<div class="side-empty">还没有 Agent，去「Agent 管理」注册。</div>';

  const count = type => navTasks.filter(item => item.type === type).length;
  const groupHtml = (key, label, total, body, extra) => {
    const open = navState.groups[key] !== false;
    return '<div class="side-group">' +
      '<div class="side-group-head">' +
        '<button type="button" class="side-group-toggle" data-group-toggle="' + key + '"' +
          ' aria-expanded="' + String(open) + '">' +
          (GROUP_ICONS[key] || "") +
          '<span>' + esc(label) + '</span><span class="count">' + total + '</span>' +
          '<span class="caret" aria-hidden="true">›</span></button>' +
        (extra || "") +
      '</div>' +
      '<div class="side-group-body"' + (open ? "" : " hidden") + '>' + body + '</div>' +
    '</div>';
  };
  // 可点击的分区入口：点击直接打开对应页面
  const linkHtml = (key, module, label) =>
    '<div class="side-group">' +
      '<div class="side-group-head">' +
        '<button type="button" class="side-group-toggle" data-module="' + module + '">' +
          (GROUP_ICONS[key] || "") + '<span>' + esc(label) + '</span></button>' +
      '</div>' +
    '</div>';
  const navRow = (module, label, extra) =>
    '<button class="side-sub-row" type="button" data-module="' + module + '">' + label + (extra || "") + '</button>';
  const auditNavRows = '<div class="side-sub">' +
    navRow("overview", "总览") +
    navRow("runs", "运行记录") +
    navRow("audit", "审计视图") +
    navRow("approvals", "审批队列", '<span class="side-count" id="side-approval-count" hidden>0</span>') +
    navRow("policies", "策略配置") +
    '</div>';
  groups.innerHTML =
    groupHtml("agents", "现有 Agent", navAgents.length, agentRows,
      '<button type="button" class="side-add" data-agents-section="register" title="注册智能体">+</button>') +
    groupHtml("agent_audit", "Agent 审计", "", auditNavRows) +
    linkHtml("code_audit", "codeaudit", "代码审计") +
    linkHtml("redteam", "redteam", "红队检测");
  updateApprovalBadge();
  syncModuleActive();
}

function runItemHtml(run, pending) {
  const waiting = pending[run.trace_id] || 0;
  return '<button type="button" class="run-item' + (navState.runId === run.trace_id ? " active" : "") +
    '" data-run-open="' + esc(run.trace_id) + '">' +
    '<span class="dot" style="background:' + verdictColor(run.verdict) + '"></span>' +
    '<span class="name">' + esc(run.trace_id) + '</span>' +
    '<span class="agent">' + esc(run.agent_id || "-") + '</span>' +
    '<span class="time">' + relTime(run.created_at) + '</span>' +
    '<span class="pending' + (waiting ? " hot" : "") + '">' + (waiting ? waiting + " 待审" : "") + '</span></button>';
}

function renderRunTimeline(pending) {
  if (!navRuns.length) return '<div class="l2-empty">暂无运行记录</div>';
  let html = "";
  let last = "";
  navRuns.forEach(run => {
    const day = dayLabel(run.created_at);
    if (day !== last) { html += '<div class="run-day">' + esc(day) + '</div>'; last = day; }
    html += runItemHtml(run, pending);
  });
  return html;
}

function renderRunAgentGroups(pending) {
  if (!navRuns.length) return '<div class="l2-empty">暂无运行记录</div>';
  const groups = {};
  navRuns.forEach(run => {
    const key = run.agent_id || "未知智能体";
    (groups[key] = groups[key] || []).push(run);
  });
  return Object.keys(groups).sort().map(agent =>
    '<div class="run-agent-group">' + esc(agent) + ' · ' + groups[agent].length + '</div>' +
    groups[agent].map(run => runItemHtml(run, pending)).join("")
  ).join("");
}

function renderRunViews() {
  const pending = pendingByTrace();
  const timeline = document.getElementById("run-list");
  const agents = document.getElementById("run-agent-list");
  if (timeline) timeline.innerHTML = renderRunTimeline(pending);
  if (agents) agents.innerHTML = renderRunAgentGroups(pending);
}

function pendingApprovalCount() {
  return navApprovals.filter(item => item.status === "proposed").length;
}

function renderGuide() {
  const pending = pendingApprovalCount();
  const states = [
    ["agents", navAgents.length > 0, navAgents.length],
    ["runs", navRuns.length > 0, navRuns.length],
    ["approvals", navRuns.length > 0 && pending === 0, pending],
    ["policies", navPolicies.length > 0, navPolicies.length],
  ];
  let completed = 0;
  states.forEach(item => {
    if (item[1]) completed += 1;
    const step = document.getElementById("guide-step-" + item[0]);
    if (step) step.classList.toggle("done", !!item[1]);
    const state = document.getElementById("guide-state-" + item[0]);
    if (state) state.textContent = item[1] ? "已完成" : "待完成";
    const count = document.getElementById("guide-count-" + item[0]);
    if (count) count.textContent = String(item[2]);
  });
  const demo = document.getElementById("guide-step-demo");
  if (demo) demo.classList.toggle("done", navRuns.length > 0);
  const ring = document.getElementById("guide-ring-value");
  if (ring) ring.style.strokeDashoffset = String(326.7 * (1 - completed / 4));
  const progress = document.getElementById("guide-progress-count");
  if (progress) progress.textContent = completed + "/4";

  let target = "agents";
  let label = "继续下一步：接入智能体";
  let next = "建议从第 1 步开始：注册智能体并签发 Key。";
  if (navAgents.length && !navRuns.length) {
    label = "继续下一步：跑一次演示";
    next = "下一步：跑一次演示或接入 SDK，然后回到运行记录。";
  } else if (navRuns.length && pending) {
    target = "approvals";
    label = "继续下一步：处理 " + pending + " 条审批";
    next = "有 " + pending + " 条审批等待处理，先去审批队列。";
  } else if (navRuns.length && !navPolicies.length) {
    target = "policies";
    label = "继续下一步：配置策略";
    next = "下一步：配置一条策略，先选「仅记录」观察。";
  } else if (completed === 4) {
    target = "runs";
    label = "查看运行记录";
    next = "全部就绪：可以继续观察运行与审批。";
  }
  const primary = document.getElementById("guide-primary");
  if (primary) {
    primary.dataset.guideGo = target;
    primary.textContent = label;
  }
  const box = document.getElementById("guide-next");
  if (box) box.textContent = next;
}

function scrollAgentsSection(key) {
  if (key === "governance") { sysSection = "data"; setModule("models"); applySysTabs(); return; }
  setModule("agents");
  let card = null;
  if (key === "manage" || key === "register") card = document.getElementById("agent-management-card");
  else if (key === "version") card = document.getElementById("version-card");
  else card = document.querySelector("#panel-agents .gateway-hint");
  if (card) {
    if (card.tagName === "DETAILS") card.open = true;
    card.scrollIntoView({block: "start"});
  }
  if (key === "register") {
    const input = document.getElementById("agent-id");
    if (input) input.focus();
  }
  closeSideOverlay();
}

document.getElementById("panel-guide").addEventListener("click", event => {
  const copy = event.target.closest("#guide-copy-demo");
  if (copy) {
    event.stopPropagation();
    navigator.clipboard.writeText("py -m safeorbit demo").then(() => {
      copy.textContent = "已复制";
    }).catch(() => {
      copy.textContent = "复制失败";
    });
    window.setTimeout(() => { copy.textContent = "复制"; }, 1500);
    return;
  }
  const go = event.target.closest("[data-guide-go]");
  if (go) setModule(go.dataset.guideGo);
});

function renderNav() {
  renderSide();
  renderAgentPage();
  renderTaskPage();
  renderAccount();
}

function renderToolbar() {
  const crumb = document.getElementById("crumb");
  if (!crumb) return;
  const meta = MODULE_META[navState.module] || MODULE_META.overview;
  let html = "<span>" + esc(meta.label) + "</span>";
  if (navState.module === "agent" && navState.agentId) {
    html += "<span>/</span><b>" + esc(navState.agentId) + "</b>";
  }
  if (navState.module === "task") {
    const task = findTask(navState.taskId);
    if (task) html += "<span>/</span><b>" + esc(task.title) + "</b>";
  }
  if (navState.module === "audit" && navState.runId) {
    html += "<span>/</span><b>" + esc(navState.runId) + "</b>";
    html += '<button type="button" class="small" data-crumb-back="1">返回运行记录</button>';
  }
  crumb.innerHTML = html;
  const views = document.getElementById("tb-views");
  if (views) views.hidden = navState.module !== "runs";
  const navBtn = document.getElementById("tb-nav");
  if (navBtn) {
    navBtn.hidden = false;
    navBtn.setAttribute("aria-expanded", narrowMode() ? String(overlayOpen) : String(!navState.collapsed));
  }
  document.querySelectorAll(".tb-seg").forEach(btn => btn.classList.toggle("active", btn.dataset.view === navState.view));
}

function applyRunView(view) {
  navState.view = view;
  document.querySelectorAll(".run-view").forEach(node => {
    node.classList.toggle("active", node.id === "run-view-" + view);
  });
  renderToolbar();
  persistNav();
}

function applyAuditVisibility() {
  const has = Boolean(navState.runId);
  const replay = document.getElementById("replay");
  const empty = document.getElementById("audit-empty");
  if (replay) replay.style.display = has ? "block" : "none";
  if (empty) empty.hidden = has;
}

function showRunDetail(traceId) {
  navState.runId = traceId;
  setModule("audit");
  navState.runId = traceId;
  applyAuditVisibility();
  renderToolbar();
  persistNav();
}

function exitRunDetail() {
  navState.runId = null;
  setModule("runs");
}

// ------------------------- Agent 工作台 -------------------------
let agentPageSig = "";
let taskPageSig = "";

function agentRecord(agentId) {
  return navAgents.find(item => item.agent_id === agentId) || null;
}

function agentRunListHtml(runs) {
  if (!runs.length) {
    return '<div class="agent-form-note">还没有该 Agent 的运行记录。先让它通过网关或 /v1/ingest 上报一条轨迹。</div>';
  }
  return '<div class="task-list">' + runs.map(run =>
    '<button type="button" class="task-row" data-run-open="' + esc(run.trace_id) + '">' +
      '<span class="dot" style="width:6px;height:6px;border-radius:50%;background:' + verdictColor(run.verdict) + '"></span>' +
      '<span>' + esc(run.trace_id) + '</span>' +
      '<span class="meta">' + esc(LABELS[run.verdict] || run.verdict || "-") + ' · ' + Number(run.hits_count || 0) + ' 命中</span>' +
      '<span class="meta">' + esc(relTime(run.created_at)) + '</span></button>').join("") + '</div>';
}

function agentTaskListHtml(items) {
  if (!items.length) return '<div class="agent-form-note">还没有任务。用上方的「新增 Agent 审计」「代码审计」「红队检测」发起。</div>';
  return '<div class="task-list">' + items.map(item =>
    '<button type="button" class="task-row" data-task-open="' + esc(item.id) + '">' +
      '<span class="dot" style="width:6px;height:6px;border-radius:50%;background:' + taskStatusColor(item.status) + '"></span>' +
      '<span>' + esc(item.title) + '</span>' +
      '<span class="meta">' + esc(TASK_TYPES[item.type].label) + '</span>' +
      '<span class="meta">' + esc(relTime(item.created_at)) + '</span></button>').join("") + '</div>';
}

function renderAgentPage(force) {
  const panel = document.getElementById("panel-agent");
  const body = document.getElementById("agent-page-body");
  if (!panel || !body) return;
  if (!panel.classList.contains("active")) return;
  const tab = navState.agentTab;
  const agent = agentRecord(navState.agentId);
  const sig = [navState.agentId, tab, navAgents.length, navRuns.length, navTasks.length, navApprovals.length].join("|");
  if (!force && sig === agentPageSig) return;
  agentPageSig = sig;

  const title = document.getElementById("agent-page-title");
  const meta = document.getElementById("agent-page-meta");
  const actions = document.getElementById("agent-page-actions");
  document.querySelectorAll("[data-agent-tab]").forEach(btn =>
    btn.classList.toggle("active", btn.dataset.agentTab === tab));

  if (!agent) {
    if (title) title.textContent = "智能体";
    if (meta) meta.textContent = "还没有选择智能体。注册后即可在这里发起审计任务。";
    if (actions) actions.innerHTML = "";
    body.innerHTML = '<div class="agent-form-note">左侧「现有 Agent」为空。先到「Agent 管理 → 注册智能体」创建，再回来发起任务。</div>';
    return;
  }

  const runs = agentRuns(agent.agent_id);
  const waiting = pendingAgentCount(agent.agent_id);
  const tasks = navTasks.filter(item => item.agent_id === agent.agent_id);
  if (title) title.textContent = agent.name || agent.agent_id;
  if (meta) {
    meta.textContent = agent.agent_id + " · " + (agent.enabled ? "已启用" : "已禁用") +
      " · 已生成报告 " + Number(agent.report_count || 0) + " 条" +
      " · 最近认证 " + (agent.last_seen || "尚未认证");
  }
  if (actions) {
    actions.innerHTML = '<button class="small" type="button" data-agent-config="' + esc(agent.agent_id) + '">接入配置与 Key</button>';
  }

  if (tab === "records") {
    const groups = ["agent_audit", "code_audit", "redteam"];
    body.innerHTML = groups.map(type => {
      const items = tasks.filter(item => item.type === type);
      return '<div class="card wide"><h2>' + esc(TASK_TYPES[type].label) + '</h2>' +
        agentTaskListHtml(items) + '</div>';
    }).join("");
    return;
  }

  const risky = runs.filter(run => run.verdict && run.verdict !== "pass").length;
  const cost = runs.reduce((sum, run) => sum + Number(run.cost_usd || 0), 0);
  body.innerHTML =
    '<div class="agent-stats">' +
      '<div class="agent-stat"><div class="k">运行记录</div><div class="v">' + runs.length +
        '</div><div class="s">' + (runs[0] ? "最近 " + esc(dayLabel(runs[0].created_at)) + " " + esc(timeText(runs[0].created_at)) : "暂无") + '</div></div>' +
      '<div class="agent-stat"><div class="k">风险记录</div><div class="v" style="color:var(--amber)">' + risky +
        '</div><div class="s">非通过判定</div></div>' +
      '<div class="agent-stat"><div class="k">待审批</div><div class="v" style="color:' + (waiting ? "var(--red)" : "inherit") + '">' + waiting +
        '</div><div class="s">写操作等待人工确认</div></div>' +
      '<div class="agent-stat"><div class="k">审计任务</div><div class="v">' + tasks.length +
        '</div><div class="s">累计成本 ' + esc(money(cost)) + '</div></div>' +
    '</div>' +
    '<div class="agent-grid">' +
      '<div class="card"><h2>最近运行</h2>' + agentRunListHtml(runs.slice(0, 6)) + '</div>' +
      '<div class="card"><h2>任务记录</h2>' + agentTaskListHtml(tasks.slice(0, 8)) + '</div>' +
    '</div>';
}

function openAgentPage(agentId) {
  if (!agentId) return;
  navState.agentId = agentId;
  navState.taskId = null;
  navState.module = "agent";
  navState.panel = "agent";
  switchTab("agent");
  agentPageSig = "";
  renderAgentPage(true);
  renderToolbar();
  persistNav();
  closeSideOverlay();
}

function setAgentTab(tab) {
  navState.agentTab = ["total", "audit", "code", "redteam"].includes(tab) ? tab : "total";
  agentPageSig = "";
  renderAgentPage(true);
  persistNav();
}

function agentFormValue(selector) {
  const node = document.querySelector(selector);
  return node ? String(node.value || "").trim() : "";
}

function agentFormStatus(selector, text) {
  const node = document.querySelector(selector);
  if (node) node.textContent = text;
}

async function runAgentAudit() {
  const agent = agentRecord(navState.agentId);
  if (!agent) return;
  const button = document.querySelector("[data-agent-audit-run]");
  const range = agentFormValue("[data-agent-audit-range]") || "all";
  const search = agentFormValue("[data-agent-audit-search]").toLowerCase();
  if (button) button.disabled = true;
  agentFormStatus("[data-agent-audit-msg]", "读取轨迹中…");
  try {
    const data = await api("/api/traces?limit=200&offset=0&agent_id=" + encodeURIComponent(agent.agent_id));
    let runs = data.traces || [];
    if (range === "24h" || range === "7d") {
      const windowMs = (range === "24h" ? 24 : 24 * 7) * 3600 * 1000;
      const since = Date.now() - windowMs;
      runs = runs.filter(run => Date.parse(run.created_at) >= since);
    }
    if (search) {
      runs = runs.filter(run =>
        String(run.trace_id || "").toLowerCase().includes(search) ||
        String(run.session_id || "").toLowerCase().includes(search));
    }
    const counts = {pass: 0, warn: 0, fail: 0, review: 0, other: 0};
    runs.forEach(run => { counts[run.verdict] = (counts[run.verdict] || 0) + 1; });
    const risky = runs.filter(run => run.verdict && run.verdict !== "pass").length;
    const cost = runs.reduce((sum, run) => sum + Number(run.cost_usd || 0), 0);
    const stamp = new Date().toLocaleString("zh-CN", {hour12: false});
    const task = addTask({
      id: newTaskId("audit"), type: "agent_audit", agent_id: agent.agent_id,
      title: (agent.name || agent.agent_id) + " · 审计 " + stamp,
      status: runs.length ? "done" : "failed",
      created_at: new Date().toISOString(),
      summary: {runs: runs.length, risky: risky, cost: Math.round(cost * 1000000) / 1000000, range: range},
      detail: {
        runs: runs.slice(0, 50).map(run => ({
          trace_id: run.trace_id, verdict: run.verdict,
          hits_count: run.hits_count, cost_usd: run.cost_usd, created_at: run.created_at,
        })),
        counts: counts,
      },
    });
    openTaskPage(task.id);
  } catch (error) {
    agentFormStatus("[data-agent-audit-msg]", error.message);
  } finally {
    if (button) button.disabled = false;
  }
}

async function runAgentCodeAudit() {
  const agent = agentRecord(navState.agentId);
  if (!agent) return;
  const path = agentFormValue("[data-agent-code-path]");
  if (!path) { agentFormStatus("[data-agent-code-msg]", "请先填写服务器本机路径"); return; }
  const button = document.querySelector("[data-agent-code-run]");
  if (button) button.disabled = true;
  agentFormStatus("[data-agent-code-msg]", "扫描中…");
  try {
    const data = await api("/api/codeaudit/scan", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: path}),
    });
    const report = data.report || {};
    const findings = report.findings || [];
    const stamp = new Date().toLocaleString("zh-CN", {hour12: false});
    const task = addTask({
      id: newTaskId("code"), type: "code_audit", agent_id: agent.agent_id,
      title: (agent.name || agent.agent_id) + " · 代码扫描 " + stamp,
      status: "done",
      created_at: new Date().toISOString(),
      summary: {files: report.files_scanned || 0, findings: findings.length},
      detail: {report_id: report.report_id || "", path: path, findings: findings.slice(0, 120)},
    });
    openTaskPage(task.id);
  } catch (error) {
    agentFormStatus("[data-agent-code-msg]", error.message);
  } finally {
    if (button) button.disabled = false;
  }
}

async function runAgentRedteam() {
  const agent = agentRecord(navState.agentId);
  if (!agent) return;
  const button = document.querySelector("[data-agent-redteam-run]");
  if (button) button.disabled = true;
  agentFormStatus("[data-agent-redteam-msg]", "运行中…");
  try {
    const data = await api("/api/eval/redteam", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({}),
    });
    const report = data.report || {};
    const stamp = new Date().toLocaleString("zh-CN", {hour12: false});
    const task = addTask({
      id: newTaskId("red"), type: "redteam", agent_id: agent.agent_id,
      title: (agent.name || agent.agent_id) + " · 红队 " + stamp,
      status: "done",
      created_at: new Date().toISOString(),
      summary: {total: report.total || 0, detected: report.detected || 0, rate: report.detection_rate || 0},
      detail: {report_id: report.report_id || "", results: report.results || []},
    });
    openTaskPage(task.id);
  } catch (error) {
    agentFormStatus("[data-agent-redteam-msg]", error.message);
  } finally {
    if (button) button.disabled = false;
  }
}

// ------------------------- 任务详情 -------------------------
function renderTaskPage(force) {
  const panel = document.getElementById("panel-task");
  const body = document.getElementById("task-body");
  if (!panel || !body || !panel.classList.contains("active")) return;
  const task = findTask(navState.taskId);
  const kind = document.getElementById("task-kind");
  const title = document.getElementById("task-title");
  const meta = document.getElementById("task-meta");
  if (!task) {
    if (kind) kind.textContent = "TASK / 任务";
    if (title) title.textContent = "任务不存在";
    if (meta) meta.textContent = "任务记录保存在本机浏览器；清理站点数据后会丢失。";
    body.innerHTML = '<div class="agent-form-note">找不到该任务。请从左侧任务组重新打开，或在 Agent 工作台发起新任务。</div>';
    return;
  }
  const sig = task.id + "|" + task.status;
  if (!force && sig === taskPageSig) return;
  taskPageSig = sig;
  const typeMeta = TASK_TYPES[task.type];
  if (kind) kind.textContent = "TASK / " + typeMeta.group;
  if (title) title.textContent = task.title;
  if (meta) {
    meta.textContent = "Agent " + task.agent_id + " · " +
      (task.status === "running" ? "进行中" : (task.status === "failed" ? "未完成" : "已完成")) +
      " · " + new Date(task.created_at).toLocaleString("zh-CN", {hour12: false});
  }

  if (task.type === "agent_audit") {
    const summary = task.summary || {};
    const counts = (task.detail && task.detail.counts) || {};
    const runs = (task.detail && task.detail.runs) || [];
    body.innerHTML =
      '<div class="agent-stats">' +
        '<div class="agent-stat"><div class="k">覆盖运行</div><div class="v">' + Number(summary.runs || 0) + '</div><div class="s">本次审计样本</div></div>' +
        '<div class="agent-stat"><div class="k">风险记录</div><div class="v" style="color:var(--amber)">' + Number(summary.risky || 0) + '</div><div class="s">非通过判定</div></div>' +
        '<div class="agent-stat"><div class="k">通过</div><div class="v" style="color:var(--green)">' + Number(counts.pass || 0) + '</div><div class="s">无命中</div></div>' +
        '<div class="agent-stat"><div class="k">成本</div><div class="v">' + esc(money(summary.cost || 0)) + '</div><div class="s">样本内模型成本</div></div>' +
      '</div>' +
      (runs.length
        ? '<div class="card"><h2>审计样本</h2>' + agentRunListHtml(runs) + '</div>'
        : '<div class="agent-form-note">该范围内没有运行记录，任务标记为未完成。调整范围后重新发起即可。</div>');
    return;
  }

  if (task.type === "code_audit") {
    const summary = task.summary || {};
    const findings = (task.detail && task.detail.findings) || [];
    body.innerHTML =
      '<div class="agent-stats">' +
        '<div class="agent-stat"><div class="k">扫描文件</div><div class="v">' + Number(summary.files || 0) + '</div><div class="s">本次范围</div></div>' +
        '<div class="agent-stat"><div class="k">问题数</div><div class="v" style="color:var(--amber)">' + Number(summary.findings || 0) + '</div><div class="s">含低危</div></div>' +
        '<div class="agent-stat"><div class="k">报告编号</div><div class="v" style="font-size:13px">' + esc((task.detail && task.detail.report_id) || "-") + '</div><div class="s">服务端可下载</div></div>' +
        '<div class="agent-stat"><div class="k">扫描路径</div><div class="v" style="font-size:12px;word-break:break-all">' + esc((task.detail && task.detail.path) || "-") + '</div><div class="s">本机目录</div></div>' +
      '</div>' +
      '<div class="card"><h2>问题明细</h2>' +
        (findings.length
          ? '<div class="table-scroll" tabindex="0" role="region" aria-label="任务代码审计结果"><table><thead><tr><th>级别</th><th>问题</th><th>位置</th><th>建议</th></tr></thead><tbody>' +
            findings.map(item =>
              '<tr><td>' + esc(item.severity || "") + '</td><td>' + esc(item.title || item.rule_id || "") +
              '</td><td><code>' + esc(item.path || "") + (item.line ? ":" + item.line : "") + '</code></td>' +
              '<td class="muted">' + esc(item.advice || "") + '</td></tr>').join("") +
            '</tbody></table></div>'
          : '<div class="agent-form-note">没有发现问题。</div>') +
      '</div>';
    return;
  }

  const summary = task.summary || {};
  const results = (task.detail && task.detail.results) || [];
  body.innerHTML =
    '<div class="agent-stats">' +
      '<div class="agent-stat"><div class="k">用例总数</div><div class="v">' + Number(summary.total || 0) + '</div><div class="s">内置攻击集</div></div>' +
      '<div class="agent-stat"><div class="k">已检出</div><div class="v" style="color:var(--green)">' + Number(summary.detected || 0) + '</div><div class="s">命中护栏规则</div></div>' +
      '<div class="agent-stat"><div class="k">检出率</div><div class="v">' + percentText(summary.rate || 0) + '</div><div class="s">越高越好</div></div>' +
      '<div class="agent-stat"><div class="k">漏报</div><div class="v" style="color:var(--red)">' + Math.max(0, Number(summary.total || 0) - Number(summary.detected || 0)) + '</div><div class="s">需要补规则</div></div>' +
    '</div>' +
    '<div class="card"><h2>用例结果</h2>' +
      '<div class="table-scroll" tabindex="0" role="region" aria-label="红队用例结果"><table><thead><tr><th>结果</th><th>提示</th><th>命中规则</th></tr></thead><tbody>' +
      results.map(item =>
        '<tr><td style="color:' + (item.detected ? "var(--green)" : "var(--red)") + '">' + (item.detected ? "已检出" : "漏报") +
        '</td><td>' + esc(item.prompt || "") + '</td><td class="muted">' + esc((item.codes || []).join("、") || "-") + '</td></tr>').join("") +
      '</tbody></table></div>' +
    '</div>';
}

function openTaskPage(taskId) {
  const task = findTask(taskId);
  if (!task) return;
  navState.taskId = taskId;
  navState.agentId = task.agent_id;
  navState.module = "task";
  navState.panel = "task";
  switchTab("task");
  taskPageSig = "";
  renderTaskPage(true);
  renderToolbar();
  persistNav();
  closeSideOverlay();
}

// ------------------------- 设置 / 身份 -------------------------
const ROLE_NAMES = {admin: "管理员", auditor: "审计员", operator: "操作员", viewer: "普通用户"};
let accountUsersLoaded = false;

function renderAccount() {
  const panel = document.getElementById("panel-account");
  if (!panel || !panel.classList.contains("active")) return;
  const box = document.getElementById("account-current");
  if (box) {
    box.innerHTML = currentUser
      ? '<div><span>账号</span><b>' + esc(currentUser.username) + '</b></div>' +
        '<div><span>显示名称</span><b>' + esc(currentUser.display_name || "-") + '</b></div>' +
        '<div><span>角色</span><b>' + esc(ROLE_NAMES[currentUser.role] || currentUser.role) + '</b></div>' +
        '<div><span>租户</span><b>' + esc(currentUser.tenant_id || "default") + '</b></div>'
      : '<div class="muted">未登录</div>';
  }
  const register = document.getElementById("account-register");
  const hint = document.getElementById("account-register-hint");
  const admin = can("admin");
  if (register) register.disabled = !admin;
  if (hint) hint.textContent = admin
    ? "管理员可创建账号；新建账号可立即用于切换身份。"
    : "仅管理员可创建账号，当前身份无权限。";
  if (admin && !accountUsersLoaded) loadAccountUsers();
}

async function loadAccountUsers() {
  const select = document.getElementById("account-switch-user");
  if (!select) return;
  try {
    const data = await api("/api/auth/users");
    const users = data.users || [];
    accountUsersLoaded = true;
    select.innerHTML = '<option value="">手动输入</option>' + users.map(user =>
      '<option value="' + esc(user.username) + '">' + esc(user.username) +
      ' · ' + esc(ROLE_NAMES[user.role] || user.role) + (user.disabled ? "（已禁用）" : "") + '</option>').join("");
  } catch (error) {}
}

async function switchAccount() {
  const message = document.getElementById("account-switch-msg");
  const select = document.getElementById("account-switch-user");
  const nameInput = document.getElementById("account-switch-name");
  const passInput = document.getElementById("account-switch-pass");
  const username = (nameInput.value || select.value || "").trim();
  const password = passInput.value || "";
  message.textContent = "";
  if (!username) { message.textContent = "请选择或填写账号"; return; }
  if (!password) { message.textContent = "请输入该账号的密码"; return; }
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-SafeOrbit-CSRF": "1"},
      body: JSON.stringify({username: username, password: password}),
    });
    const data = await response.json();
    if (!response.ok) { message.textContent = data.detail || "切换失败"; return; }
    passInput.value = "";
    message.textContent = "已切换为 " + username + "，正在刷新…";
    window.setTimeout(() => window.location.reload(), 400);
  } catch (error) {
    message.textContent = "切换失败：" + error.message;
  }
}

async function registerAccount() {
  const message = document.getElementById("account-register-msg");
  const username = document.getElementById("account-new-name").value.trim();
  const display = document.getElementById("account-new-display").value.trim();
  const password = document.getElementById("account-new-pass").value;
  const role = document.getElementById("account-new-role").value;
  message.textContent = "";
  if (!username || !password) { message.textContent = "用户名与初始密码必填"; return; }
  try {
    await api("/api/auth/users", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({username: username, password: password, role: role, display_name: display}),
    });
    document.getElementById("account-new-name").value = "";
    document.getElementById("account-new-display").value = "";
    document.getElementById("account-new-pass").value = "";
    message.textContent = "已创建 " + username + "（" + (ROLE_NAMES[role] || role) + "）";
    accountUsersLoaded = false;
    await loadAccountUsers();
  } catch (error) {
    message.textContent = error.message;
  }
}

// ------------------------- 模块切换 -------------------------
function setModule(module) {
  if (!MODULE_META[module]) return;
  navState.module = module;
  navState.panel = MODULE_META[module].panel;
  if (module !== "audit") navState.runId = null;
  if (module !== "task") navState.taskId = null;
  document.querySelectorAll(".run-view").forEach(node => {
    node.classList.toggle("active", node.id === "run-view-" + navState.view);
  });
  switchTab(navState.panel);
  overlayOpen = false;
  applyResponsive();
  renderToolbar();
  persistNav();
  closeSideOverlay();
}

function afterPanelSwitch(panel) {
  navState.panel = panel;
  if (PANEL_TO_MODULE[panel]) navState.module = PANEL_TO_MODULE[panel];
  if (panel !== "audit") navState.runId = null;
  syncModuleActive();
  renderSide();
  renderAgentPage();
  renderTaskPage();
  renderAccount();
  renderToolbar();
  applyResponsive();
  persistNav();
}

function openDrawer() {
  const drawer = document.getElementById("drawer");
  const scrim = document.getElementById("drawer-scrim");
  if (!drawer || !scrim) return;
  drawer.hidden = false;
  scrim.hidden = false;
  requestAnimationFrame(() => drawer.classList.add("open"));
}

function closeDrawer() {
  const drawer = document.getElementById("drawer");
  const scrim = document.getElementById("drawer-scrim");
  if (!drawer || !scrim) return;
  drawer.classList.remove("open");
  window.setTimeout(() => {
    if (!drawer.classList.contains("open")) { drawer.hidden = true; scrim.hidden = true; }
  }, 190);
}

function updateApprovalBadge() {
  const pending = navApprovals.filter(item => item.status === "proposed").length;
  const badge = document.getElementById("side-approval-count");
  if (!badge) return;
  badge.hidden = pending === 0;
  badge.textContent = pending > 99 ? "99+" : String(pending);
}

async function refreshNav(force) {
  const now = Date.now();
  if (!force && now - navLoadedAt < 4000) return;
  navLoadedAt = now;
  try {
    const [traces, approvals, policies, agents] = await Promise.all([
      api("/api/traces?limit=200&offset=0"),
      api("/api/approvals"),
      api("/api/policies"),
      api("/api/agents"),
    ]);
    navRuns = traces.traces || [];
    navApprovals = approvals.approvals || [];
    navPolicies = policies.policies || [];
    navAgents = agents.agents || [];
    updateApprovalBadge();
    renderNav();
    renderRunViews();
    renderGuide();
  } catch (error) {}
}

document.getElementById("l3-body").addEventListener("click", event => {
  const run = event.target.closest("[data-run-open]");
  if (run) openReplay(run.dataset.runOpen);
});

// 侧边栏点击与模块折叠
document.getElementById("side-body").addEventListener("click", event => {
  const groupToggle = event.target.closest("[data-group-toggle]");
  if (groupToggle) {
    const key = groupToggle.dataset.groupToggle;
    navState.groups[key] = navState.groups[key] === false;
    persistNav();
    renderSide();
    return;
  }
  const moduleBtn = event.target.closest("[data-module]");
  if (moduleBtn) {
    setModule(moduleBtn.dataset.module);
    return;
  }
  const agentRow = event.target.closest("[data-agent-row]");
  if (agentRow) { openAgentPage(agentRow.dataset.agentRow); return; }
  const taskRow = event.target.closest("[data-task-row]");
  if (taskRow) { openTaskPage(taskRow.dataset.taskRow); return; }
  const agentsSection = event.target.closest("[data-agents-section]");
  if (agentsSection) { scrollAgentsSection(agentsSection.dataset.agentsSection); }
});

document.getElementById("side-body").addEventListener("keydown", event => {
  if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
  const rows = Array.from(document.querySelectorAll("#side-body .side-item, #side-body .side-sub-row, #side-body .side-group-toggle"));
  const index = rows.indexOf(document.activeElement);
  if (index < 0) return;
  event.preventDefault();
  const next = rows[Math.min(rows.length - 1, Math.max(0, index + (event.key === "ArrowDown" ? 1 : -1)))];
  if (next) next.focus();
});

// Agent 工作台交互
document.getElementById("agent-page-body").addEventListener("click", event => {
  if (event.target.closest("[data-agent-audit-run]")) { runAgentAudit(); return; }
  if (event.target.closest("[data-agent-code-run]")) { runAgentCodeAudit(); return; }
  if (event.target.closest("[data-agent-redteam-run]")) { runAgentRedteam(); return; }
  if (event.target.closest("[data-code-open-panel]")) { setModule("codeaudit"); return; }
  const task = event.target.closest("[data-task-open]");
  if (task) { openTaskPage(task.dataset.taskOpen); return; }
  const config = event.target.closest("[data-agent-config]");
  if (config) {
    setModule("agents");
    selectAgent(config.dataset.agentConfig);
    const card = document.getElementById("agent-detail");
    if (card) card.scrollIntoView({block: "start"});
  }
});

document.querySelectorAll("[data-agent-tab]").forEach(button => {
  button.addEventListener("click", () => setAgentTab(button.dataset.agentTab));
});

document.getElementById("task-back").addEventListener("click", () => {
  if (navState.agentId) openAgentPage(navState.agentId); else setModule("agents");
});
document.getElementById("task-open-agent").addEventListener("click", () => {
  if (navState.agentId) openAgentPage(navState.agentId); else setModule("agents");
});

// 设置 / 身份
document.getElementById("account-logout").addEventListener("click", doLogout);
document.getElementById("account-switch").addEventListener("click", switchAccount);
document.getElementById("account-register").addEventListener("click", registerAccount);
document.getElementById("account-switch-user").addEventListener("change", event => {
  if (event.target.value) document.getElementById("account-switch-name").value = event.target.value;
});

// 工具条
document.querySelectorAll(".tb-seg").forEach(button => {
  button.addEventListener("click", () => applyRunView(button.dataset.view));
});
document.getElementById("tb-filter").addEventListener("click", () => {
  setModule("runs");
  const filters = document.getElementById("trace-filters");
  if (filters) { filters.open = true; filters.scrollIntoView({block: "center"}); }
});
document.getElementById("tb-export").addEventListener("click", () => {
  if (navState.module === "runs") document.getElementById("summary-export").click();
  else document.getElementById("f-export").click();
});
document.getElementById("tb-search").addEventListener("click", openPalette);
document.getElementById("tb-nav").addEventListener("click", toggleSide);
document.getElementById("crumb").addEventListener("click", event => {
  if (event.target.closest("[data-crumb-back]")) exitRunDetail();
});

document.getElementById("side-collapse").addEventListener("click", toggleSide);
document.getElementById("side-user").addEventListener("click", () => setModule("account"));
document.getElementById("side-scrim").addEventListener("click", closeSideOverlay);
document.getElementById("drawer-close").addEventListener("click", closeDrawer);
document.getElementById("drawer-scrim").addEventListener("click", closeDrawer);
window.addEventListener("resize", applyResponsive);

// 宽度拖拽（240–360）
(function bindSideResizer() {
  const handle = document.getElementById("side-resizer");
  if (!handle) return;
  let dragging = false;
  handle.addEventListener("mousedown", event => {
    if (narrowMode()) return;
    event.preventDefault();
    dragging = true;
    document.body.style.userSelect = "none";
  });
  window.addEventListener("mousemove", event => {
    if (!dragging) return;
    navState.width = Math.min(360, Math.max(232, event.clientX));
    navState.collapsed = false;
    applyResponsive();
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    document.body.style.userSelect = "";
    persistNav();
  });
})();

// 全局命令面板
let paletteIndex = 0;
let paletteCurrent = [];

function buildPaletteEntries() {
  const entries = [];
  Object.keys(MODULE_META).forEach(key => entries.push({
    type: "module", group: "模块", title: MODULE_META[key].label, hint: "跳转", value: key,
  }));
  entries.push({type: "action", group: "操作", title: "打开筛选", hint: "运行记录", run: () => {
    setModule("runs");
    const filters = document.getElementById("trace-filters");
    if (filters) { filters.open = true; filters.scrollIntoView({block: "center"}); }
  }});
  entries.push({type: "action", group: "操作", title: "导出记录 CSV", hint: "运行记录", run: () => {
    setModule("runs");
    document.getElementById("f-export").click();
  }});
  entries.push({type: "action", group: "操作", title: navState.collapsed ? "展开侧边栏" : "折叠侧边栏", hint: "布局", run: toggleSide});
  navAgents.forEach(agent => entries.push({
    type: "agent", group: "现有 Agent", title: agent.name || agent.agent_id,
    hint: agent.agent_id + " · " + agentRuns(agent.agent_id).length + " 运行", value: agent.agent_id,
  }));
  navTasks.slice(0, 60).forEach(task => entries.push({
    type: "task", group: "任务", title: task.title,
    hint: TASK_TYPES[task.type].label + " · " + (task.status === "done" ? "已完成" : "未完成"), value: task.id,
  }));
  navRuns.slice(0, 80).forEach(run => entries.push({
    type: "run", group: "运行", title: run.trace_id,
    hint: (run.agent_id || "") + " · " + relTime(run.created_at), value: run.trace_id,
  }));
  navApprovals.forEach(item => entries.push({
    type: "approval", group: "审批", title: item.tool_name + " · " + item.agent_id,
    hint: item.status === "proposed" ? countdownText(item.expires_at) : (item.status_text || item.status),
    value: item.approval_id,
  }));
  navPolicies.forEach(item => entries.push({
    type: "policy", group: "策略", title: item.name || item.agent_id,
    hint: item.mode === "enforce" ? "允许阻断" : "仅记录", value: item.agent_id,
  }));
  if (auditViewData && auditViewData.resources) {
    auditViewData.resources.forEach(item => entries.push({
      type: "resource", group: "当前运行的资源", title: item.resource,
      hint: (item.kind_label || "") + " · " + ((item.tools || []).join("、") || "-"),
      value: (item.span_ids || [])[0] || "",
    }));
  }
  return entries;
}

function renderPalette() {
  const input = document.getElementById("palette-input");
  const list = document.getElementById("palette-list");
  const query = (input.value || "").trim().toLowerCase();
  const all = buildPaletteEntries();
  paletteCurrent = query
    ? all.filter(item => (item.title + " " + item.group + " " + item.hint).toLowerCase().includes(query))
    : all;
  paletteIndex = Math.min(paletteIndex, Math.max(0, paletteCurrent.length - 1));
  if (!paletteCurrent.length) {
    list.innerHTML = '<div class="l2-empty">没有匹配结果</div>';
    return;
  }
  let html = "";
  let group = "";
  paletteCurrent.forEach((item, index) => {
    if (item.group !== group) {
      html += '<div class="palette-group">' + esc(item.group) + '</div>';
      group = item.group;
    }
    html += '<button type="button" class="palette-item' + (index === paletteIndex ? " active" : "") + '" data-palette-index="' + index + '">' +
      '<span>' + esc(item.title) + '</span><span class="hint">' + esc(item.hint || "") + '</span></button>';
  });
  list.innerHTML = html;
}

function openPalette() {
  const palette = document.getElementById("palette");
  const scrim = document.getElementById("palette-scrim");
  if (!palette) return;
  palette.hidden = false;
  scrim.hidden = false;
  paletteIndex = 0;
  document.getElementById("palette-input").value = "";
  renderPalette();
  document.getElementById("palette-input").focus();
}

function closePalette() {
  const palette = document.getElementById("palette");
  const scrim = document.getElementById("palette-scrim");
  if (!palette) return;
  palette.hidden = true;
  scrim.hidden = true;
}

function togglePalette() {
  const palette = document.getElementById("palette");
  if (palette.hidden) openPalette(); else closePalette();
}

function movePalette(step) {
  if (!paletteCurrent.length) return;
  paletteIndex = (paletteIndex + step + paletteCurrent.length) % paletteCurrent.length;
  renderPalette();
  const active = document.querySelector(".palette-item.active");
  if (active) active.scrollIntoView({block: "nearest"});
}

function executePalette(item) {
  closePalette();
  if (item.type === "module") setModule(item.value);
  else if (item.type === "agent") openAgentPage(item.value);
  else if (item.type === "task") openTaskPage(item.value);
  else if (item.type === "run") { openReplay(item.value); }
  else if (item.type === "approval") { setModule("approvals"); showApprovalDetail(item.value); }
  else if (item.type === "policy") setModule("policies");
  else if (item.type === "resource" && item.value) { if (navState.runId) setModule("audit"); locateSpan(item.value); }
  else if (item.type === "action" && item.run) item.run();
}

document.getElementById("palette-input").addEventListener("input", () => { paletteIndex = 0; renderPalette(); });
document.getElementById("palette-input").addEventListener("keydown", event => {
  if (event.key === "Escape") { closePalette(); return; }
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    movePalette(event.key === "ArrowDown" ? 1 : -1);
    return;
  }
  if (event.key === "Enter") {
    event.preventDefault();
    if (paletteCurrent[paletteIndex]) executePalette(paletteCurrent[paletteIndex]);
  }
});
document.getElementById("palette-list").addEventListener("click", event => {
  const button = event.target.closest("[data-palette-index]");
  if (button && paletteCurrent[Number(button.dataset.paletteIndex)]) {
    executePalette(paletteCurrent[Number(button.dataset.paletteIndex)]);
  }
});
document.getElementById("palette-scrim").addEventListener("click", closePalette);

document.addEventListener("keydown", event => {
  const modifier = event.metaKey || event.ctrlKey;
  if (modifier && event.key.toLowerCase() === "k") {
    event.preventDefault();
    togglePalette();
    return;
  }
  if (event.key !== "Escape") return;
  const palette = document.getElementById("palette");
  if (palette && !palette.hidden) { closePalette(); return; }
  const drawer = document.getElementById("drawer");
  if (drawer && !drawer.hidden) { closeDrawer(); return; }
  if (overlayOpen) { overlayOpen = false; applyResponsive(); }
});

document.getElementById("replay-list").addEventListener("keydown", event => {
  const row = event.target.closest(".rrow[data-index]");
  if (!row) return;
  if (event.key === "Enter") { event.preventDefault(); openSpanDetail(Number(row.dataset.index)); return; }
  if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
  event.preventDefault();
  const rows = Array.from(document.querySelectorAll("#replay-list .rrow"));
  const index = rows.indexOf(row);
  const next = rows[Math.min(rows.length - 1, Math.max(0, index + (event.key === "ArrowDown" ? 1 : -1)))];
  if (next) next.focus();
});

function restoreRoute() {
  switchTab(navState.panel || "guide");
  if (navState.module === "agent" && navState.agentId) openAgentPage(navState.agentId);
  if (navState.module === "task" && navState.taskId) openTaskPage(navState.taskId);
  if (navState.module === "audit" && navState.runId) openReplay(navState.runId);
}

loadNavState();
loadTasks();
applyResponsive();
syncModuleActive();
renderSide();
renderToolbar();

// ------------------------- 初始化 -------------------------
let eventSource = null;
function connectEvents() {
  if (eventSource) return;
  eventSource = new EventSource("/api/events");
  eventSource.addEventListener("ready", () => {
    document.getElementById("mode").textContent =
      (document.getElementById("mode").textContent || "") || "影子模式 · 在线";
  });
  eventSource.addEventListener("alert", event => {
    try {
      const item = JSON.parse(event.data);
      alerts.unshift(item);
      renderAlerts();
      // 实时提醒在审计视图下方，新提醒到达时自动展开，避免被折叠遗漏。
      const alertsCard = document.getElementById("alerts-card");
      if (alertsCard && !alertsCard.open) alertsCard.open = true;
      refreshTraces();
      refreshSummary(true);
      refreshNav();
      if (document.getElementById("panel-dispositions").classList.contains("active")) loadDispositions();
    } catch (error) {}
  });
  eventSource.onerror = () => {
    document.getElementById("mode").textContent = "事件流断开，轮询中";
  };
}

function refreshAll() {
  refreshTraces();
  loadHotl();
  refreshNav();
  if (document.getElementById("panel-traces").classList.contains("active")) refreshSummary(false);
  if (document.getElementById("panel-policies").classList.contains("active")) loadPolicies();
  if (document.getElementById("panel-dispositions").classList.contains("active")) loadDispositions();
  if (document.getElementById("panel-codeaudit").classList.contains("active")) loadCodeAudit();
  if (document.getElementById("panel-agents").classList.contains("active")) loadAgents();
  if (document.getElementById("panel-settings").classList.contains("active")) {
    loadModels();
    loadModelSettings();
  }
}

let hotlLoadedAt = 0;
async function loadHotl(force) {
  const now = Date.now();
  if (!force && now - hotlLoadedAt < 30000) return;
  try {
    const data = await api("/api/audit/metrics");
    hotlLoadedAt = Date.now();
    document.getElementById("hotl-escalation").textContent = percentText(data.escalation_rate);
    document.getElementById("hotl-escalation-sub").textContent =
      Number(data.risk_traces || 0) + " / " + Number(data.traces || 0) + " 条记录";
    document.getElementById("hotl-rejection").textContent = percentText(data.rejection_rate);
    document.getElementById("hotl-rejection-sub").textContent = data.decided
      ? "已决策 " + Number(data.decided) + " 条 · 待审 " + Number(data.pending || 0)
      : "暂无已决策审批";
    document.getElementById("hotl-drift").textContent = percentText(data.drift_rate);
    document.getElementById("hotl-drift-sub").textContent =
      Number(data.drift_traces || 0) + " 条命中基线偏离";
    document.getElementById("hotl-decision").textContent = secondsText(data.avg_decision_seconds);
    document.getElementById("hotl-decision-sub").textContent =
      data.avg_decision_seconds !== null && data.avg_decision_seconds < 10
        ? "决策过快，注意橡皮图章风险"
        : "基于已决策审批";
    document.getElementById("hotl-cost").textContent = money(data.cost_7d);
    const change = data.cost_change;
    document.getElementById("hotl-cost-sub").textContent = change === null || change === undefined
      ? "无可比周期"
      : "较上一周期 " + (change >= 0 ? "+" : "") + (Number(change) * 100).toFixed(1) + "%";
    document.getElementById("hotl-meta").textContent = "更新于 " + timeText(data.generated_at);
    document.getElementById("hotl-note").textContent = "";
  } catch (error) {
    document.getElementById("hotl-note").textContent = "指标加载失败：" + error.message;
  }
}

document.getElementById("hotl-card").addEventListener("click", event => {
  const item = event.target.closest(".hotl-item[data-hotl]");
  if (!item) return;
  const action = item.dataset.hotl;
  if (action === "escalation") {
    switchTab("dispositions");
    document.getElementById("hotl-note").textContent = "已跳转到风险处置：查看升级为人工的处置提案。";
    return;
  }
  if (action === "rejection" || action === "decision") {
    switchTab("dispositions");
    const card = document.getElementById("approval-card");
    if (card) {
      card.open = true;
      card.scrollIntoView({behavior: "smooth", block: "center"});
    }
    document.getElementById("hotl-note").textContent = "已打开写操作审批队列。";
    return;
  }
  if (action === "drift") {
    document.getElementById("f-verdict").value = "review";
    traceOffset = 0;
    refreshTraces();
    const filters = document.getElementById("trace-filters");
    if (filters) filters.open = true;
    document.getElementById("hotl-note").textContent = "已按“待复核”筛选轨迹。";
    return;
  }
  if (action === "cost") {
    const summary = document.getElementById("summary");
    if (summary) {
      summary.open = true;
      summary.scrollIntoView({behavior: "smooth", block: "start"});
    }
    document.getElementById("hotl-note").textContent = "已打开审计汇总，可按智能体查看成本。";
  }
});

(async function start() {
  try { setupLandingReveal(); } catch (error) {}
  loadLandingHealth();
  startLandingMotion();
  if (await loadMe()) {
    restoreRoute();
    refreshAll();
    connectEvents();
  }
  setInterval(() => { if (currentUser) refreshAll(); }, 5000);
})();
</script>
</body>
</html>
"""
