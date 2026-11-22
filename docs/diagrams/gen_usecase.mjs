// 用例图生成脚本。修改下面 DIAGRAMS 中的数据后运行：
//   node gen_usecase.mjs          → 生成同目录下的 uc-*.svg
//   node gen_usecase.mjs --png    → 另外用本机 Chrome 渲染出 uc-*.png（2 倍分辨率，便于插入 Word）
import fs from 'fs';
import path from 'path';
import os from 'os';
import { execFileSync } from 'child_process';
import { fileURLToPath } from 'url';

const DIR = path.dirname(fileURLToPath(import.meta.url));
const FONT = "'Microsoft YaHei','PingFang SC','Noto Sans CJK SC',sans-serif";

// ---------- 图数据 ----------
// 角色用例图：一个主角色在左侧，系统边界内按模块分包；inc 表示该用例被上一行的某用例 «include»
const DIAGRAMS = [
  {
    file: 'uc-overview', kind: 'overview', title: '教务系统顶层用例图',
    left: ['学生', '任课教师'],
    right: ['管理员'],
    ucs: ['学生信息管理', '课程与选课管理', '考试管理', '成绩管理', '评教管理', '系统管理'],
    links: {
      学生: ['学生信息管理', '课程与选课管理', '考试管理', '成绩管理', '评教管理'],
      任课教师: ['课程与选课管理', '考试管理', '成绩管理', '评教管理'],
      管理员: ['学生信息管理', '课程与选课管理', '考试管理', '成绩管理', '评教管理', '系统管理'],
    },
  },
  {
    file: 'uc-student-1', title: '学生用例图（一）', actor: '学生',
    pkgs: [
      { name: '学生信息管理', ucs: ['S01 查看学籍卡片', 'S02 申请修改个人信息', 'S03 维护家庭成员信息', 'S04 查看学籍异动记录'] },
      { name: '课程与选课管理', ucs: ['K04 查看培养方案/执行计划', 'K05 查看培养方案完成情况', 'K10 选课/退选', { label: 'K10a 选课校验', inc: 'K10 选课/退选' }, 'K12 查询选课结果', 'K13 重修/补修报名', 'K15 免听申请', 'K16 查看个人课表', 'K17 查询公共课表'] },
    ],
  },
  {
    file: 'uc-student-2', title: '学生用例图（二）', actor: '学生',
    pkgs: [
      { name: '考试管理', ucs: ['E03 查询考试安排', 'E05 缓考申请'] },
      { name: '成绩管理', ucs: ['G06 查询课程成绩', 'G07 成绩复核申请'] },
      { name: '评教管理', ucs: ['P03 进行评教'] },
    ],
  },
  {
    file: 'uc-teacher', title: '任课教师用例图', actor: '任课教师',
    pkgs: [
      { name: '课程与选课管理', ucs: ['K16 查看个人课表', 'K18 查看教学班名单', 'K19 调停课申请'] },
      { name: '考试管理', ucs: ['E04 查询监考安排'] },
      { name: '成绩管理', ucs: ['G02 设置成绩构成', 'G03 录入/导入成绩', 'G04 提交成绩', 'G08 处理成绩复核', 'G09 成绩更正申请', 'G11 成绩统计分析'] },
      { name: '评教管理', ucs: ['P04 查看本人评教结果'] },
    ],
  },
  {
    file: 'uc-admin-1', title: '管理员用例图（一）', actor: '管理员',
    pkgs: [
      { name: '课程与选课管理', ucs: ['K01 维护学期与基础数据', 'K02 维护课程库', 'K03 维护培养方案', 'K06 生成开课计划', 'K07 安排任课教师', 'K08 排课', { label: 'K08a 时间与教室冲突检测', inc: 'K08 排课' }, 'K09 设置选课轮次与规则', 'K11 选课筛选与调整', 'K14 审核重修/免听申请', 'K20 审批调停课'] },
    ],
    extraInc: [['K20 审批调停课', 'K08a 时间与教室冲突检测']],
  },
  {
    file: 'uc-admin-2', title: '管理员用例图（二）', actor: '管理员',
    pkgs: [
      { name: '学生信息管理', ucs: ['S05 审核信息修改申请', 'S06 导入新生/分班', 'S07 办理学籍异动', 'S08 查询统计学生信息'] },
      { name: '考试管理', ucs: ['E01 排考（含补缓考）', 'E02 安排监考', 'E06 审核缓考申请'] },
      { name: '成绩管理', ucs: ['G01 设置成绩录入时间', 'G05 审核并发布成绩', 'G10 审批成绩更正', 'G11 成绩统计分析', 'G12 维护绩点规则'] },
    ],
  },
  {
    file: 'uc-admin-3', title: '管理员用例图（三）', actor: '管理员',
    pkgs: [
      { name: '评教管理', ucs: ['P01 维护评价指标体系', 'P02 发布评教批次', 'P05 查看评教结果', 'P06 监控评教进度'] },
      { name: '系统管理', ucs: ['A01 账户管理', 'A02 角色与权限配置', 'A03 同步教师/教室数据', 'A04 查看操作日志'] },
    ],
  },
];

// ---------- 绘图 ----------
const UC_W = 210, UC_H = 36, UC_H2 = 46, ROW_GAP = 10;
const PKG_PAD = 12, PKG_HEAD = 24, PKG_GAP = 14, PKG_EXTRA = 96; // 右侧留白放 «include» 弧线
const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const tw = (s, size) => [...s].reduce((w, ch) => w + (/[\x00-\xff]/.test(ch) ? size * 0.56 : size), 0);

function wrap(label, max) {
  if (tw(label, 13) <= max) return [label];
  const chars = [...label];
  let cut = Math.ceil(chars.length / 2);
  const sp = label.indexOf(' ');
  if (sp > 0 && tw(label.slice(sp + 1), 13) <= max) cut = sp + 1;
  return [chars.slice(0, cut).join('').trim(), chars.slice(cut).join('').trim()];
}

function actorSvg(x, y, name) {
  const s = 'stroke="#333" stroke-width="1.6" fill="none"';
  return `<g><circle cx="${x}" cy="${y}" r="9" fill="#fff" stroke="#333" stroke-width="1.6"/>`
    + `<line x1="${x}" y1="${y + 9}" x2="${x}" y2="${y + 34}" ${s}/>`
    + `<line x1="${x - 15}" y1="${y + 18}" x2="${x + 15}" y2="${y + 18}" ${s}/>`
    + `<line x1="${x}" y1="${y + 34}" x2="${x - 12}" y2="${y + 52}" ${s}/>`
    + `<line x1="${x}" y1="${y + 34}" x2="${x + 12}" y2="${y + 52}" ${s}/>`
    + `<text x="${x}" y="${y + 72}" text-anchor="middle" font-size="14" font-weight="bold" stroke="#fff" stroke-width="5" paint-order="stroke">${esc(name)}</text></g>`;
}

function ucSvg(cx, cy, w, h, lines, dashed = false) {
  const t = lines.length === 1
    ? `<text x="${cx}" y="${cy + 5}" text-anchor="middle" font-size="13">${esc(lines[0])}</text>`
    : `<text x="${cx}" y="${cy - 3}" text-anchor="middle" font-size="13">${esc(lines[0])}</text>`
      + `<text x="${cx}" y="${cy + 13}" text-anchor="middle" font-size="13">${esc(lines[1])}</text>`;
  return `<ellipse cx="${cx}" cy="${cy}" rx="${w / 2}" ry="${h / 2}" fill="#fff" stroke="#333" stroke-width="1.3"${dashed ? ' stroke-dasharray="4 3"' : ''}/>` + t;
}

function wrapSvg(w, h, body) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" font-family="${FONT}">`
    + `<defs><marker id="open" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" orient="auto-start-reverse">`
    + `<path d="M0,0 L10,5 L0,10" fill="none" stroke="#444" stroke-width="1.4"/></marker></defs>`
    + `<rect width="${w}" height="${h}" fill="#fff"/>${body}</svg>`;
}

function roleDiagram(d) {
  const sysX = 150, sysY = 46, actorX = 66;
  const pkgX = sysX + 24, pkgW = PKG_PAD * 2 + UC_W + PKG_EXTRA;
  let y = sysY + 40, body = '';
  const pos = {}; // label -> {cx, cy, h}
  const incs = [...(d.extraInc || [])];
  const linked = [];
  for (const p of d.pkgs) {
    const items = p.ucs.map((u) => (typeof u === 'string' ? { label: u } : u));
    const hs = items.map((u) => (wrap(u.label, UC_W - 30).length > 1 ? UC_H2 : UC_H));
    const ph = PKG_HEAD + PKG_PAD * 2 + hs.reduce((a, b) => a + b, 0) + ROW_GAP * (items.length - 1);
    // 包：带标签页的矩形
    body += `<path d="M${pkgX},${y} h${tw(p.name, 13) + 20} l6,${PKG_HEAD - 4} H${pkgX + pkgW} V${y + ph} H${pkgX} Z" fill="#f4f7fb" stroke="#8a9bb5" stroke-width="1.2"/>`;
    body += `<line x1="${pkgX}" y1="${y + PKG_HEAD - 4}" x2="${pkgX + tw(p.name, 13) + 26}" y2="${y + PKG_HEAD - 4}" stroke="#8a9bb5" stroke-width="1.2"/>`;
    body += `<text x="${pkgX + 10}" y="${y + 16}" font-size="13" font-weight="bold" fill="#3d4f6b">${esc(p.name)}</text>`;
    let uy = y + PKG_HEAD + PKG_PAD;
    items.forEach((u, i) => {
      const h = hs[i], cx = pkgX + PKG_PAD + UC_W / 2, cy = uy + h / 2;
      pos[u.label] = { cx, cy, h };
      body += ucSvg(cx, cy, UC_W, h, wrap(u.label, UC_W - 30));
      if (u.inc) incs.push([u.inc, u.label]); else linked.push(u.label);
      uy += h + ROW_GAP;
    });
    y += ph + PKG_GAP;
  }
  const sysH = y - sysY + 10, sysW = pkgW + 48;
  const W = sysX + sysW + 20, H = sysY + sysH + 20;
  let pre = `<text x="${W / 2}" y="26" text-anchor="middle" font-size="16" font-weight="bold">${esc(d.title)}</text>`;
  pre += `<rect x="${sysX}" y="${sysY}" width="${sysW}" height="${sysH}" fill="none" stroke="#333" stroke-width="1.5"/>`;
  pre += `<text x="${sysX + 12}" y="${sysY + 22}" font-size="14" font-weight="bold">教务系统</text>`;
  // 主角色放在其关联用例的纵向中点
  const ys = linked.map((l) => pos[l].cy);
  const ay = Math.max(sysY + 20, (Math.min(...ys) + Math.max(...ys)) / 2 - 22);
  let lines = '';
  for (const l of linked) {
    const p = pos[l];
    lines += `<line x1="${actorX + 18}" y1="${ay + 20}" x2="${p.cx - UC_W / 2}" y2="${p.cy}" stroke="#555" stroke-width="1"/>`;
  }
  // «include»：从基用例右侧画弧线指向被包含用例
  for (const [from, to] of incs) {
    const a = pos[from], b = pos[to];
    const x0 = a.cx + UC_W / 2 - 18, x1 = b.cx + UC_W / 2 - 18;
    const bend = 30 + Math.min(40, Math.abs(b.cy - a.cy) / 6);
    const up = b.cy < a.cy; // 被包含用例在基用例上方时，弧线从基用例顶部出发
    const yFrom = up ? a.cy - a.h / 2 + 6 : a.cy + a.h / 2 - 8;
    const yTo = up ? b.cy + b.h / 2 - 6 : b.cy - b.h / 2 + 6;
    lines += `<path d="M${x0},${yFrom} C${x0 + bend},${yFrom} ${x1 + bend},${yTo} ${x1},${yTo}" fill="none" stroke="#444" stroke-width="1.1" stroke-dasharray="5 3" marker-end="url(#open)"/>`;
    lines += `<text x="${Math.max(x0, x1) + bend - 4}" y="${(yFrom + yTo) / 2 + 4}" font-size="11" fill="#444">«include»</text>`;
  }
  return wrapSvg(W, H, pre + body + lines + actorSvg(actorX, ay, d.actor));
}

function overviewDiagram(d) {
  const W = 760, ucW = 190, ucH = 42, gap = 22;
  const sysX = 230, sysW = W - 2 * sysX, sysY = 50;
  const cx = W / 2;
  const top = sysY + 44;
  const pos = {};
  d.ucs.forEach((u, i) => { pos[u] = top + ucH / 2 + i * (ucH + gap); });
  const sysH = d.ucs.length * (ucH + gap) + 50;
  const H = sysY + sysH + 30;
  let s = `<text x="${W / 2}" y="28" text-anchor="middle" font-size="16" font-weight="bold">${esc(d.title)}</text>`;
  s += `<rect x="${sysX}" y="${sysY}" width="${sysW}" height="${sysH}" fill="none" stroke="#333" stroke-width="1.5"/>`;
  s += `<text x="${sysX + 12}" y="${sysY + 22}" font-size="14" font-weight="bold">教务系统</text>`;
  const place = (names, x, side) => {
    const span = sysH - 40, step = span / names.length;
    names.forEach((n, i) => {
      const ay = sysY + 20 + step * i + step / 2 - 36;
      for (const u of d.links[n]) {
        const ex = side === 'L' ? cx - ucW / 2 : cx + ucW / 2;
        s += `<line x1="${x + (side === 'L' ? 18 : -18)}" y1="${ay + 20}" x2="${ex}" y2="${pos[u]}" stroke="#666" stroke-width="1"/>`;
      }
      s += actorSvg(x, ay, n);
    });
  };
  place(d.left, 110, 'L');
  place(d.right, W - 110, 'R');
  d.ucs.forEach((u) => { s += ucSvg(cx, pos[u], ucW, ucH, [u]); });
  return wrapSvg(W, H, s);
}

const outputs = [];
for (const d of DIAGRAMS) {
  const svg = d.kind === 'overview' ? overviewDiagram(d) : roleDiagram(d);
  const f = path.join(DIR, d.file + '.svg');
  fs.writeFileSync(f, svg, 'utf8');
  const [, w, h] = svg.match(/width="(\d+(?:\.\d+)?)" height="(\d+(?:\.\d+)?)"/);
  outputs.push({ f, w: Math.ceil(+w), h: Math.ceil(+h) });
  console.log('svg', d.file, `${Math.ceil(+w)}x${Math.ceil(+h)}`);
}

if (process.argv.includes('--png')) {
  const chrome = process.env.CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
  const profile = fs.mkdtempSync(path.join(process.env.TEMP || os.tmpdir(), 'uc-chrome-'));
  for (const o of outputs) {
    const html = o.f.replace(/\.svg$/, '.tmp.html');
    fs.writeFileSync(html, `<!doctype html><meta charset="utf-8"><style>html,body{margin:0}</style>${fs.readFileSync(o.f, 'utf8')}`);
    execFileSync(chrome, ['--headless=new', '--disable-gpu', '--hide-scrollbars', `--user-data-dir=${profile}`,
      '--force-device-scale-factor=2', `--window-size=${o.w},${o.h}`, `--screenshot=${o.f.replace(/\.svg$/, '.png')}`,
      'file:///' + html.replace(/\\/g, '/')], { stdio: 'ignore' });
    fs.unlinkSync(html);
    console.log('png', path.basename(o.f, '.svg'));
  }
  fs.rmSync(profile, { recursive: true, force: true });
}
