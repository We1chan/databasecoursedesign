// E-R 图生成脚本。实体用矩形，联系直接画成连线：联系名写在线旁，两端标多重性（1、0..*、1..*）。
// 坐标手工排布，保证连线不交叉，“排课”居中。
//   node gen_er.mjs          → 生成 er-overview.svg
//   node gen_er.mjs --png    → 另外用本机 Chrome 渲染 er-overview.png（2 倍分辨率）
import fs from 'fs';
import path from 'path';
import os from 'os';
import { execFileSync } from 'child_process';
import { fileURLToPath } from 'url';

const DIR = path.dirname(fileURLToPath(import.meta.url));
const FONT = "'Microsoft YaHei','PingFang SC','Noto Sans CJK SC',sans-serif";

// 实体中心坐标
const ENTITIES = {
  学院: [110, 70], 专业: [110, 220], 培养方案: [380, 220], 课程: [640, 220], 学年学期: [900, 220],
  教师: [330, 430], 排课: [640, 430], 教室: [950, 430],
  考试: [950, 640],
  行政班: [110, 790], 学生: [640, 790],
};
const CORE = '排课';

// [联系名, 实体A, 实体B, A 端多重性, B 端多重性, 选项]
// 多重性写在哪一端：B 端的数字表示“一个 A 对应几个 B”。
// 选项：name / mult 为联系名、多重性放在连线的哪一侧（+1 / -1，以 A→B 方向的左法线为 +1），
//      multA / multB 单独覆盖某一端多重性的位置；
//      pa / pb 为连线在 A、B 边框上的接点（相对实体中心），不给则沿连线自动求边框交点。
//      排课周围连线多，逐条指定接点，避免多重性标注挤在角上。
const RELATIONS = [
  ['下设', '学院', '专业', '1', '0..*', { name: 1, mult: -1 }],
  ['包含', '专业', '行政班', '1', '0..*', { name: 1, mult: 1 }],
  ['编入', '行政班', '学生', '1', '0..*', { name: -1, mult: 1 }],
  ['制定', '专业', '培养方案', '1', '0..*', { name: -1, mult: 1 }],
  ['规定', '培养方案', '课程', '0..*', '1..*', { name: -1, mult: 1 }],
  ['开设', '课程', '排课', '1', '0..*', { name: -1, mult: 1, pa: [-25, 21], pb: [-25, -28] }],
  ['属于', '学年学期', '排课', '1', '0..*', { name: 1, mult: 1, multA: -1, pb: [45, -28] }],
  ['授课', '教师', '排课', '1', '0..*', { name: -1, mult: -1 }],
  ['占用', '排课', '教室', '0..*', '1..*', { name: -1, mult: -1 }],
  ['面向', '排课', '行政班', '0..*', '0..*', { name: 1, mult: -1, pa: [-60, 28], pb: [40, -21] }],
  ['选课', '学生', '排课', '0..*', '0..*', { name: -1, mult: 1, pa: [-25, -21], pb: [-25, 28] }],
  ['评价', '学生', '排课', '0..*', '0..*', { name: 1, mult: 1, pa: [25, -21], pb: [25, 28] }],
  ['安排', '排课', '考试', '1', '0..*', { name: -1, mult: 1, multB: -1, pa: [85, 18] }],
  ['参加', '学生', '考试', '1..*', '0..*', { name: 1, mult: 1, pa: [50, 0] }],
];

const EW = 100, EH = 42, CW = 170, CH = 56; // 普通实体、核心实体的宽高
const W = 1080, H = 850;
const INK = '#18212d', LINE = '#3a4250', ACCENT = '#1d4f91';

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
const size = (n) => (n === CORE ? [CW, CH] : [EW, EH]);
const norm = ([x, y]) => { const l = Math.hypot(x, y); return [x / l, y / l]; };

// 从点 c 沿方向 d 走到以 center 为中心的矩形边界
function rectEdge(center, c, [dx, dy], [w, h]) {
  const rx = w / 2 - Math.abs(c[0] - center[0]), ry = h / 2 - Math.abs(c[1] - center[1]);
  const t = Math.min(rx / Math.abs(dx || 1e-9), ry / Math.abs(dy || 1e-9));
  return [c[0] + dx * t, c[1] + dy * t];
}
// 在 (x, y) 处放文字，按偏移方向 (nx, ny) 决定对齐方式，使文字整体落在该方向一侧
function text(x, y, s, [nx, ny], { size: fs = 14, color = INK, weight = 400 } = {}) {
  const anchor = nx > 0.35 ? 'start' : nx < -0.35 ? 'end' : 'middle';
  const yy = y + fs * 0.36 + ny * fs * 0.45;
  return `<text x="${x.toFixed(1)}" y="${yy.toFixed(1)}" text-anchor="${anchor}" font-size="${fs}" font-weight="${weight}" fill="${color}" stroke="#fff" stroke-width="4" paint-order="stroke">${esc(s)}</text>`;
}

let lines = '', labels = '', boxes = '';
for (const [name, a, b, ma, mb, o] of RELATIONS) {
  const A = ENTITIES[a], B = ENTITIES[b];
  const fixA = o.pa && [A[0] + o.pa[0], A[1] + o.pa[1]];
  const fixB = o.pb && [B[0] + o.pb[0], B[1] + o.pb[1]];
  // 没有指定接点的一端：从实体中心朝另一端的接点（或中心）方向求边框交点
  const pa = fixA || rectEdge(A, A, norm([(fixB || B)[0] - A[0], (fixB || B)[1] - A[1]]), size(a));
  const pb = fixB || rectEdge(B, B, norm([pa[0] - B[0], pa[1] - B[1]]), size(b));
  const d = norm([pb[0] - pa[0], pb[1] - pa[1]]);
  const n = [-d[1], d[0]]; // A→B 方向的左法线
  lines += `<line x1="${pa[0].toFixed(1)}" y1="${pa[1].toFixed(1)}" x2="${pb[0].toFixed(1)}" y2="${pb[1].toFixed(1)}" stroke="${LINE}" stroke-width="1.5"/>`;
  // 联系名：连线中点，向一侧偏 12px
  const mid = [(pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2];
  const sn = [n[0] * o.name, n[1] * o.name];
  labels += text(mid[0] + sn[0] * 12, mid[1] + sn[1] * 12, name, sn, { size: 15, weight: 500 });
  // 多重性：距实体边 16px，向一侧偏 9px；multA / multB 可单独指定某一端放哪一侧
  for (const [p, dir, m, side] of [[pa, d, ma, o.multA ?? o.mult], [pb, [-d[0], -d[1]], mb, o.multB ?? o.mult]]) {
    const sm = [n[0] * side, n[1] * side];
    labels += text(p[0] + dir[0] * 16 + sm[0] * 9, p[1] + dir[1] * 16 + sm[1] * 9, m, sm, { size: 13, color: ACCENT });
  }
}
for (const [n, [x, y]] of Object.entries(ENTITIES)) {
  const [w, h] = size(n), core = n === CORE;
  boxes += `<rect x="${x - w / 2}" y="${y - h / 2}" width="${w}" height="${h}" fill="${core ? '#e8eef8' : '#fff'}" stroke="${core ? ACCENT : LINE}" stroke-width="${core ? 2.4 : 1.5}"/>`
    + `<text x="${x}" y="${y + 6}" text-anchor="middle" font-size="${core ? 18 : 16}" font-weight="${core ? 700 : 500}" fill="${core ? ACCENT : INK}">${esc(n)}</text>`;
}
// 图例
const [lx, ly] = [W - 290, 34];
const legend = `<g font-size="13" fill="#4a5466">`
  + `<rect x="${lx}" y="${ly}" width="34" height="20" fill="#fff" stroke="${LINE}" stroke-width="1.2"/><text x="${lx + 44}" y="${ly + 15}">实体</text>`
  + `<line x1="${lx + 92}" y1="${ly + 10}" x2="${lx + 140}" y2="${ly + 10}" stroke="${LINE}" stroke-width="1.5"/><text x="${lx + 148}" y="${ly + 15}">联系（线旁为联系名）</text>`
  + `<text x="${lx}" y="${ly + 44}"><tspan fill="${ACCENT}">1</tspan> 恰好一个　<tspan fill="${ACCENT}">0..*</tspan> 零个或多个　<tspan fill="${ACCENT}">1..*</tspan> 至少一个</text></g>`;

const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" font-family="${FONT}">`
  + `<rect width="${W}" height="${H}" fill="#fff"/>${lines}${boxes}${labels}${legend}</svg>`;
const out = path.join(DIR, 'er-overview.svg');
fs.writeFileSync(out, svg, 'utf8');
console.log('svg er-overview', `${W}x${H}`);

if (process.argv.includes('--png')) {
  const chrome = process.env.CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
  const profile = fs.mkdtempSync(path.join(process.env.TEMP || os.tmpdir(), 'er-chrome-'));
  const html = out.replace(/\.svg$/, '.tmp.html');
  fs.writeFileSync(html, `<!doctype html><meta charset="utf-8"><style>html,body{margin:0}</style>${svg}`);
  execFileSync(chrome, ['--headless=new', '--disable-gpu', '--hide-scrollbars', `--user-data-dir=${profile}`,
    '--force-device-scale-factor=2', `--window-size=${W},${H}`, `--screenshot=${out.replace(/\.svg$/, '.png')}`,
    'file:///' + html.split(path.sep).join('/')], { stdio: 'ignore' });
  fs.unlinkSync(html);
  fs.rmSync(profile, { recursive: true, force: true });
  console.log('png er-overview');
}
