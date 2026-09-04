/*
 * app.js — UI と絞り込みロジック
 *
 * データの読み込み順:
 *   1. data/bundle.js が読み込まれていれば window.HS_DATA を使う（file:// でも動く）
 *   2. なければ data/*.json を fetch する（ローカルサーバー経由で開いた場合）
 */
(function () {
  'use strict';

  const $ = (s) => document.querySelector(s);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  let DATA = null;
  let stationIndex = new Map();

  // ---------- 起動 ----------
  async function boot() {
    try {
      DATA = window.HS_DATA || (await fetchJson());
    } catch (e) {
      $('#summary').innerHTML =
        '<strong>データを読み込めませんでした。</strong><br>' +
        'data/bundle.js が無い場合は <code>python tools/build_bundle.py</code> を実行してください。';
      console.error(e);
      return;
    }
    HSTransit.build(DATA.lines.lines);
    fillStations();
    bindEvents();
    render();
  }

  async function fetchJson() {
    const [lines, schools] = await Promise.all([
      fetch('data/lines.json').then((r) => r.json()),
      fetch('data/schools.json').then((r) => r.json())
    ]);
    return { lines, schools };
  }

  function fillStations() {
    const list = HSTransit.stationList();
    const dl = $('#station-list');
    list.forEach((s) => {
      stationIndex.set(s.name, s);
      const o = el('option');
      o.value = s.name;
      o.label = s.lines.join(' / ');
      dl.appendChild(o);
    });
    $('#station-hint').textContent =
      '収録駅 ' + list.length + '駅（' + DATA.lines.lines.length + '路線）。駅名を入力すると候補が出ます。';
  }

  function bindEvents() {
    $('#conditions').addEventListener('input', (e) => {
      if (e.target.id === 'limit') $('#limit-out').textContent = e.target.value;
      render();
    });
    $('#conditions').addEventListener('change', render);
  }

  // ---------- 条件の読み取り ----------
  function readConditions() {
    const modes = Array.from(document.querySelectorAll('input[name=mode]:checked')).map((i) => i.value);
    const types = Array.from(document.querySelectorAll('input[name=type]:checked')).map((i) => i.value);
    return {
      gender: document.querySelector('input[name=gender]:checked').value,
      stationName: $('#station').value.trim(),
      modes,
      types,
      access: $('#access').value,
      limit: Number($('#limit').value),
      current: Number($('#current').value),
      target: Number($('#target').value),
      narrow: $('#narrow').checked,
      sort: $('#sort').value
    };
  }

  // ---------- 合否見込みの判定 ----------
  function judge(current, target, dev) {
    const d = current - dev;
    if (d >= 8) return { key: 'safe', label: '安全圏' };
    if (d >= 3) return { key: 'likely', label: '合格圏' };
    if (d >= -2) return { key: 'even', label: '実力相応' };
    if (target - dev >= -2) return { key: 'challenge', label: '挑戦圏' };
    return { key: 'far', label: '現状では厳しい' };
  }
  const JUDGE_RANK = { safe: 0, likely: 1, even: 2, challenge: 3, far: 4 };

  function genderOk(school, want) {
    if (want === 'any') return true;
    if (school.gender === 'coed') return true;
    if (want === 'male') return school.gender === 'boys';
    return school.gender === 'girls';
  }

  const fmtMin = (m) => Math.round(m);

  // ---------- 描画 ----------
  function render() {
    const c = readConditions();
    const box = $('#results');
    box.innerHTML = '';

    const station = stationIndex.get(c.stationName);
    if (!station) {
      $('#summary').textContent = c.stationName
        ? '「' + c.stationName + '」は収録されていません。候補から選んでください。'
        : '最寄り駅を入力してください。';
      return;
    }
    if (c.modes.length === 0 || c.types.length === 0) {
      $('#summary').textContent = '通学手段と学校種別を1つ以上選んでください。';
      return;
    }

    const rows = [];
    let cutByTime = 0;
    let cutByDev = 0;

    DATA.schools.schools.forEach((s) => {
      if (!c.types.includes(s.type)) return;
      if (!genderOk(s, c.gender)) return;

      const devs = s.courses.map((x) => x.deviation);
      const minDev = Math.min.apply(null, devs);
      const maxDev = Math.max.apply(null, devs);

      if (c.narrow) {
        const ceiling = Math.max(c.current, c.target) + 4;
        const floor = c.current - 14;
        if (minDev > ceiling || maxDev < floor) {
          cutByDev++;
          return;
        }
      }

      const r = HSTransit.route(station, s, { modes: c.modes, stationAccess: c.access });
      if (!r.best) return;
      if (r.best.minutes > c.limit) {
        cutByTime++;
        return;
      }

      // 学科ごとの判定。学校としての判定は「最も届きやすい学科」を採用する。
      const courseJudges = s.courses.map((x) => ({
        name: x.name,
        deviation: x.deviation,
        j: judge(c.current, c.target, x.deviation)
      }));
      const bestJudge = courseJudges.reduce((a, b) =>
        JUDGE_RANK[a.j.key] <= JUDGE_RANK[b.j.key] ? a : b
      ).j;

      rows.push({ school: s, route: r, minDev, maxDev, courseJudges, bestJudge });
    });

    rows.sort((a, b) => {
      if (c.sort === 'dev-desc') return b.maxDev - a.maxDev || a.route.best.minutes - b.route.best.minutes;
      if (c.sort === 'dev-asc') return a.minDev - b.minDev || a.route.best.minutes - b.route.best.minutes;
      return a.route.best.minutes - b.route.best.minutes;
    });

    const notes = [];
    if (cutByTime) notes.push(cutByTime + '校が上限時間オーバー');
    if (cutByDev) notes.push(cutByDev + '校が偏差値レンジ外');
    $('#summary').innerHTML =
      '<strong>' + rows.length + '校</strong> が条件に合いました（' +
      station.name + '駅 / ' + c.limit + '分以内）' +
      (notes.length ? '<br>除外：' + notes.join('、') : '');

    if (rows.length === 0) {
      box.appendChild(
        el('div', 'empty', '条件に合う高校がありません。上限時間を延ばすか、「狙える範囲だけに絞る」を外してみてください。')
      );
      return;
    }
    rows.forEach((row) => box.appendChild(card(row, c)));
  }

  function card(row, c) {
    const s = row.school;
    const best = row.route.best;
    const node = el('article', 'school' + (s.type === 'private' ? ' private' : ''));

    // --- 制服の図（カード右側にうっすら敷く） ---
    // 実写があればそれを、無ければ制服の種類を表す自作の図案を出す。
    const art = uniformArt(s);
    if (art) node.appendChild(art);

    // --- 見出し ---
    const top = el('div', 'school-top');
    top.appendChild(el('h3', 'school-name', s.name));
    top.appendChild(el('span', 'tag ' + s.type, s.type === 'public' ? '公立' : '私立'));
    if (s.gender === 'boys') top.appendChild(el('span', 'tag boys', '男子校'));
    if (s.gender === 'girls') top.appendChild(el('span', 'tag girls', '女子校'));
    const jb = el('span', 'judge ' + row.bestJudge.key, row.bestJudge.label);
    top.appendChild(jb);
    if (!s.verified) top.appendChild(el('span', 'tag unverified', '未検証'));
    node.appendChild(top);
    node.appendChild(el('div', 'school-meta', s.city + '　' + s.address));

    // --- 主要項目 ---
    const grid = el('dl', 'school-grid');

    const modeLabel = { walk: '徒歩', bike: '自転車', bus: 'バス', train: '電車' };
    const timeCell = el('div', 'cell');
    timeCell.appendChild(el('dt', null, '通学時間（片道・概算）'));
    const tdd = el('dd', 'time-big');
    tdd.textContent = fmtMin(best.minutes) + '分';
    const sub = el('small');
    sub.textContent =
      '　' + modeLabel[best.mode] +
      (best.mode === 'train' ? '（乗換' + best.transfers + '回）' : '');
    tdd.appendChild(sub);
    timeCell.appendChild(tdd);
    grid.appendChild(timeCell);

    grid.appendChild(
      cell('偏差値（参考値）', row.minDev === row.maxDev ? String(row.maxDev) : row.minDev + '〜' + row.maxDev)
    );

    // 男女比
    const gc = el('div', 'cell');
    gc.appendChild(el('dt', null, '男女比'));
    if (s.genderRatio) {
      const dd = el('dd');
      dd.textContent = '男 ' + s.genderRatio.male + '％ / 女 ' + s.genderRatio.female + '％';
      const bar = el('div', 'ratio-bar');
      const m = el('i', 'm'); m.style.width = s.genderRatio.male + '%';
      const f = el('i', 'f'); f.style.width = s.genderRatio.female + '%';
      bar.appendChild(m); bar.appendChild(f);
      dd.appendChild(bar);
      gc.appendChild(dd);
    } else {
      gc.appendChild(el('dd', 'muted', '未取得'));
    }
    grid.appendChild(gc);

    grid.appendChild(
      cell('制服', s.uniform ? s.uniform.type + (s.uniform.note ? '（' + s.uniform.note + '）' : '') : '未取得',
        !s.uniform)
    );

    const ns = HSTransit.nearestStation(s);
    grid.appendChild(
      cell('学校の最寄り駅', ns ? ns.name + '（' + ns.lineName + '）徒歩' + fmtMin(ns.walkMin) + '分' : '—', !ns)
    );
    node.appendChild(grid);

    if (s.dataWarnings && s.dataWarnings.length) {
      s.dataWarnings.forEach((w) => node.appendChild(el('p', 'data-warning', '⚠ ' + w)));
    }

    // --- 学科ごとの判定 ---
    const courses = el('div', 'courses');
    row.courseJudges.forEach((cj) => {
      const r = el('div', 'course-row');
      r.appendChild(el('span', 'dev', String(cj.deviation)));
      r.appendChild(el('span', 'cname', cj.name));
      r.appendChild(el('span', 'judge ' + cj.j.key, cj.j.label));
      courses.appendChild(r);
    });
    node.appendChild(courses);

    // --- ルート内訳 ---
    const det = el('details', 'route');
    det.appendChild(el('summary', null, '通学ルートの内訳と他の手段を見る'));
    const ul = el('ul', 'legs');
    best.legs.forEach((l) => {
      const li = el('li');
      li.appendChild(el('span', 'lmin', fmtMin(l.minutes) + '分'));
      li.appendChild(el('span', null, l.label));
      ul.appendChild(li);
    });
    det.appendChild(ul);

    const others = Object.values(row.route.byMode)
      .filter((r) => r.mode !== best.mode)
      .sort((a, b) => a.minutes - b.minutes)
      .map((r) => modeLabel[r.mode] + ' ' + fmtMin(r.minutes) + '分' + (r.approx ? '（粗い概算）' : ''));
    if (others.length) det.appendChild(el('p', 'hint', '他の手段：' + others.join('　/　')));
    node.appendChild(det);

    // --- リンク ---
    const p = el('p', 'school-links');
    if (s.website) {
      p.appendChild(link(s.website, '公式サイト'));
      p.appendChild(document.createTextNode(' ／ '));
    }
    p.appendChild(
      link(
        'https://www.openstreetmap.org/?mlat=' + s.lat + '&mlon=' + s.lng + '#map=17/' + s.lat + '/' + s.lng,
        '地図で位置を確認'
      )
    );
    p.appendChild(document.createTextNode(
      s.updatedAt ? '　（公式サイト最終取得 ' + s.updatedAt + '）' : '　（公式サイトの自動取得は未実行）'
    ));
    node.appendChild(p);
    return node;
  }

  /**
   * カード右側に敷く制服の図。
   * uniformImage（実写）があれば優先し、無ければ制服の種類から自作SVGを描く。
   * 制服が未取得の学校には何も出さない（無いものをそれらしく見せない）。
   */
  function uniformArt(s) {
    if (s.uniformImage) {
      const box = el('div', 'uniform-art photo');
      const img = new Image();
      img.src = s.uniformImage;
      img.alt = '';
      img.loading = 'lazy';
      // 画像が見つからないときは枠ごと消す（壊れた画像アイコンを出さない）
      img.addEventListener('error', () => box.remove());
      box.appendChild(img);
      return box;
    }
    if (!s.uniform || !s.uniform.type) return null;
    const svg = HSUniformArt.svgFor(s.uniform.type);
    if (!svg) return null;
    const box = el('div', 'uniform-art');
    box.innerHTML = svg;
    return box;
  }

  function link(href, text) {
    const a = el('a', null, text);
    a.href = href;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    return a;
  }

  function cell(label, value, muted) {
    const c = el('div', 'cell');
    c.appendChild(el('dt', null, label));
    c.appendChild(el('dd', muted ? 'muted' : null, value));
    return c;
  }

  boot();
})();
