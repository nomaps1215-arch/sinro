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
    loadSettings();
    bindEvents();
    render();
  }

  // ---------- 画面の切り替え ----------
  function showSettings(on) {
    $('#view-search').hidden = on;
    $('#view-settings').hidden = !on;
    document.body.classList.toggle('settings-open', on);
    window.scrollTo(0, 0);
    if (on) $('#station').focus();
  }

  // ---------- 設定の保存（次に開いたときも同じ条件で始められるように） ----------
  const STORE_KEY = 'hs-search-conditions-v1';

  /** 設定として保存・復元する入力欄。種別ボタンと偏差値は検索画面側にある。 */
  function settingsInputs() {
    return document.querySelectorAll(
      '#conditions input, #conditions select, .type-toggle input, .dev-bar input');
  }

  function saveSettings() {
    try {
      const data = {};
      settingsInputs().forEach((i) => {
        const k = i.id || i.name + ':' + i.value;
        data[k] = i.type === 'checkbox' || i.type === 'radio' ? i.checked : i.value;
      });
      localStorage.setItem(STORE_KEY, JSON.stringify(data));
    } catch (e) {
      /* プライベートブラウズなどで保存できないことがある。動作には影響しない。 */
    }
  }

  function loadSettings() {
    let data = null;
    try {
      data = JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
    } catch (e) {
      data = null;
    }
    if (data) {
      settingsInputs().forEach((i) => {
        const k = i.id || i.name + ':' + i.value;
        if (!(k in data)) return;
        if (i.type === 'checkbox' || i.type === 'radio') i.checked = !!data[k];
        else i.value = data[k];
      });
      $('#limit-out').textContent = $('#limit').value;
    }
    // 駅が未設定なら、まず設定画面から始めてもらう
    if (!$('#station').value.trim()) showSettings(true);
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
    const onChange = (e) => {
      if (e && e.target && e.target.id === 'limit') $('#limit-out').textContent = e.target.value;
      saveSettings();
      render();
    };
    $('#conditions').addEventListener('input', onChange);
    $('#conditions').addEventListener('change', onChange);
    document.querySelector('.type-toggle').addEventListener('change', onChange);
    document.querySelector('.dev-bar').addEventListener('input', onChange);
    $('#q').addEventListener('input', render);

    $('#openSettings').addEventListener('click', () => showSettings(true));
    $('#closeSettings').addEventListener('click', () => showSettings(false));
    $('#applySettings').addEventListener('click', () => showSettings(false));
  }

  // ---------- 条件の読み取り ----------
  function readConditions() {
    const modes = Array.from(document.querySelectorAll('input[name=mode]:checked')).map((i) => i.value);
    const types = Array.from(document.querySelectorAll('input[name=type]:checked')).map((i) => i.value);
    const divisions = Array.from(document.querySelectorAll('input[name=division]:checked')).map((i) => i.value);
    return {
      q: $('#q').value.trim(),
      gender: document.querySelector('input[name=gender]:checked').value,
      stationName: $('#station').value.trim(),
      modes,
      types,
      divisions,
      showUnknownDev: $('#showUnknownDev').checked,
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

  /** 学校名・旧校名・略称のいずれかに、入力した文字が含まれていれば一致とみなす。 */
  function nameMatcher(q) {
    const norm = (s) => (s || '').replace(/\s|　/g, '').replace(/ヶ/g, 'ケ').replace(/が丘/g, 'ケ丘');
    const needle = norm(q);
    return (s) =>
      norm(s.name).includes(needle) ||
      norm(s.shortName).includes(needle) ||
      norm(s.formerName).includes(needle) ||
      norm(s.city).includes(needle);
  }

  // ---------- 描画 ----------
  function render() {
    const c = readConditions();
    const box = $('#results');
    box.innerHTML = '';

    const station = stationIndex.get(c.stationName);
    if (!station && !c.q) {
      $('#summary').textContent = c.stationName
        ? '「' + c.stationName + '」は収録されていません。設定画面で候補から選んでください。'
        : '最寄り駅を設定するか、学校名で検索してください。';
      return;
    }
    if (!c.q && (c.modes.length === 0 || c.types.length === 0)) {
      $('#summary').textContent = '通学手段と学校種別を1つ以上選んでください。';
      return;
    }

    const rows = [];
    let cutByTime = 0;
    let cutByDev = 0;
    let cutByUnknownDev = 0;
    let noCoord = 0;

    const byName = c.q ? nameMatcher(c.q) : null;

    DATA.schools.schools.forEach((s) => {
      // 名前で探しているときは、他の絞り込みを全部無視して名前だけで拾う。
      // そうしないと「上限60分」に阻まれて目当ての学校が出てこない。
      // 公立・私立の切り替えは検索欄の隣にあるので、名前検索中も効かせる
      if (!c.types.includes(s.type)) return;
      if (byName) {
        if (!byName(s)) return;
      } else {
        if (s.division && !c.divisions.includes(s.division)) return;
        if (!genderOk(s, c.gender)) return;
      }
      if (s.lat == null || s.lng == null) {
        noCoord++;
        return;
      }

      const devs = s.courses.map((x) => x.deviation).filter((d) => d != null);
      const hasDev = devs.length > 0;
      const minDev = hasDev ? Math.min.apply(null, devs) : null;
      const maxDev = hasDev ? Math.max.apply(null, devs) : null;

      if (!byName) {
        if (!hasDev) {
          // 偏差値が入っていない学校は偏差値で絞り込めない。表示するかどうかだけを選ばせる。
          if (!c.showUnknownDev) {
            cutByUnknownDev++;
            return;
          }
        } else if (c.narrow) {
          const ceiling = Math.max(c.current, c.target) + 4;
          const floor = c.current - 14;
          if (minDev > ceiling || maxDev < floor) {
            cutByDev++;
            return;
          }
        }
      }

      const r = station
        ? HSTransit.route(station, s, { modes: c.modes, stationAccess: c.access })
        : null;
      if (!byName) {
        if (!r || !r.best) return;
        if (r.best.minutes > c.limit) {
          cutByTime++;
          return;
        }
      }

      // 学科ごとの判定。学校としての判定は「最も届きやすい学科」を採用する。
      const courseJudges = s.courses.map((x) => ({
        name: x.name,
        deviation: x.deviation,
        estimated: !!x.estimated,
        j: x.deviation == null ? null : judge(c.current, c.target, x.deviation)
      }));
      const judged = courseJudges.filter((x) => x.j);
      const bestJudge = judged.length
        ? judged.reduce((a, b) => (JUDGE_RANK[a.j.key] <= JUDGE_RANK[b.j.key] ? a : b)).j
        : null;

      rows.push({ school: s, route: r, minDev, maxDev, hasDev, courseJudges, bestJudge });
    });

    // 偏差値が無い学校は数値比較できないので、偏差値順のときは末尾にまとめる
    const devKey = (r, hi) => (r.hasDev ? (hi ? r.maxDev : r.minDev) : (hi ? -Infinity : Infinity));
    const minutes = (r) => (r.route && r.route.best ? r.route.best.minutes : Infinity);
    rows.sort((a, b) => {
      if (c.sort === 'dev-desc') return devKey(b, true) - devKey(a, true) || minutes(a) - minutes(b);
      if (c.sort === 'dev-asc') return devKey(a, false) - devKey(b, false) || minutes(a) - minutes(b);
      return minutes(a) - minutes(b);
    });

    if (c.q) {
      $('#summary').innerHTML =
        '「' + c.q + '」で <strong>' + rows.length + '校</strong>' +
        (station ? '' : '<br>最寄り駅が未設定のため、通学時間は出ません。');
    } else {
      const notes = [];
      if (cutByTime) notes.push(cutByTime + '校が上限時間オーバー');
      if (cutByDev) notes.push(cutByDev + '校が偏差値レンジ外');
      if (cutByUnknownDev) notes.push(cutByUnknownDev + '校が偏差値未入力');
      if (noCoord) notes.push(noCoord + '校が位置データなしで計算不可');
      $('#summary').innerHTML =
        '<strong>' + rows.length + '校</strong> が条件に合いました' +
        (notes.length ? '<br>除外：' + notes.join('、') : '');
    }

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
    const best = row.route && row.route.best;   // 名前検索だけのときは経路が無い
    const node = el('article', 'school' + (s.type === 'private' ? ' private' : ''));

    // --- 制服の図（カード右側にうっすら敷く） ---
    // 実写があればそれを、無ければ制服の種類を表す自作の図案を出す。
    const art = uniformArt(s);
    if (art) node.appendChild(art);

    // --- タペストリー（カードの上端から垂らす縦書きの札） ---
    // 左＝公立/私立、右＝昨年の定員割れ。ひと目で属性が分かるようにする。
    const left = el('span', 'tapestry left ' + s.type, s.type === 'public' ? '公立' : '私立');
    node.appendChild(left);
    if (s.lastYearUnderCapacity) {
      const t = el('span', 'tapestry right under', '昨年定員割れ');
      t.title = s.lastYearUnderCapacityNote || '昨年度の入学者選抜で志願者数が募集人員に届かなかった学校です。';
      node.appendChild(t);
      node.classList.add('has-right-tapestry');
    }

    // --- 見出し ---
    const top = el('div', 'school-top');
    top.appendChild(el('h3', 'school-name', s.name));
    if (s.gender === 'boys') top.appendChild(el('span', 'tag boys', '男子校'));
    if (s.gender === 'girls') top.appendChild(el('span', 'tag girls', '女子校'));
    if (s.division && s.division !== '全日制') top.appendChild(el('span', 'tag division', s.division));
    if (row.bestJudge) {
      top.appendChild(el('span', 'judge ' + row.bestJudge.key, row.bestJudge.label));
    }
    node.appendChild(top);
    const where = [s.city, s.address].filter(Boolean).join('　');
    if (where) node.appendChild(el('div', 'school-meta', where));

    // --- 主要項目 ---
    const grid = el('dl', 'school-grid');

    const modeLabel = { walk: '徒歩', bike: '自転車', bus: 'バス', train: '電車' };
    if (best) {
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
    }

    // 偏差値。公的なデータが存在しないので、確度に応じて出し分ける。
    //   estimated: true の値は模試の公表値ではなく推定なので「想定」と添える。
    if (row.hasDev) {
      const dc = el('div', 'cell');
      const estimated = s.courses.some((x) => x.deviation != null && x.estimated);
      dc.appendChild(el('dt', null, estimated ? '偏差値（想定）' : '偏差値（参考値）'));
      const dd = el('dd', 'dev-big',
        row.minDev === row.maxDev ? String(row.maxDev) : row.minDev + '〜' + row.maxDev);
      if (estimated) {
        dd.appendChild(el('small', 'est', '（想定）'));
        dd.title = '模試の公表値ではなく、周辺校との比較から見積もった数値です。';
      }
      dc.appendChild(dd);
      grid.appendChild(dc);
    } else {
      grid.appendChild(cell('偏差値', '公表データなし', true,
        '高校の偏差値に公的なデータは存在しません。模試の資料などを見て手で登録する必要があります。'));
    }

    // 男女比と制服は、公式サイトに載っている学校だけ取得できている。
    // 大半が未取得で「未取得」の行だけが並ぶと邪魔なので、値があるときだけ出す。
    if (s.genderRatio) {
      const gc = el('div', 'cell');
      gc.appendChild(el('dt', null, '男女比'));
      const dd = el('dd');
      dd.textContent = '男 ' + s.genderRatio.male + '％ / 女 ' + s.genderRatio.female + '％';
      const bar = el('div', 'ratio-bar');
      const m = el('i', 'm'); m.style.width = s.genderRatio.male + '%';
      const f = el('i', 'f'); f.style.width = s.genderRatio.female + '%';
      bar.appendChild(m); bar.appendChild(f);
      dd.appendChild(bar);
      gc.appendChild(dd);
      grid.appendChild(gc);
    }
    if (s.uniform) {
      grid.appendChild(
        cell('制服', s.uniform.type + (s.uniform.note ? '（' + s.uniform.note + '）' : ''))
      );
    }

    // 最寄り駅の見出し行の右端に「通学ルート」ボタンを置く。
    // 値と同じ行に並べると、スマホ幅では駅名が長くて折り返してしまう。
    const ns = HSTransit.nearestStation(s);
    const nsCell = el('div', 'cell wide');
    const head = el('div', 'cell-head');
    head.appendChild(el('dt', null, '学校の最寄り駅'));
    const routeBtn = el('button', 'btn route-btn', '通学ルート');
    routeBtn.type = 'button';
    routeBtn.setAttribute('aria-expanded', 'false');
    if (best) head.appendChild(routeBtn);
    nsCell.appendChild(head);
    nsCell.appendChild(el('dd', ns ? null : 'muted',
      ns ? ns.name + '（' + ns.lineName + '）徒歩' + fmtMin(ns.walkMin) + '分' : '—'));
    grid.appendChild(nsCell);
    node.appendChild(grid);

    if (s.dataWarnings && s.dataWarnings.length) {
      s.dataWarnings.forEach((w) => node.appendChild(el('p', 'data-warning', '⚠ ' + w)));
    }

    // --- 学科ごとの判定 ---
    const courses = el('div', 'courses');
    row.courseJudges.forEach((cj) => {
      const r = el('div', 'course-row');
      r.appendChild(el('span', 'dev' + (cj.deviation == null ? ' none' : ''),
        cj.deviation == null ? '—' : String(cj.deviation) + (cj.estimated ? '*' : '')));
      r.appendChild(el('span', 'cname', cj.name));
      if (cj.j) r.appendChild(el('span', 'judge ' + cj.j.key, cj.j.label));
      courses.appendChild(r);
    });
    node.appendChild(courses);

    // --- ルート内訳（最寄り駅の右のボタンで開閉する） ---
    if (best) {
      const det = el('div', 'route-panel');
      det.hidden = true;
      routeBtn.addEventListener('click', () => {
        det.hidden = !det.hidden;
        routeBtn.setAttribute('aria-expanded', String(!det.hidden));
        routeBtn.classList.toggle('open', !det.hidden);
      });
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
    }

    // --- ボタン ---
    const p = el('div', 'school-links');
    if (s.website) p.appendChild(button(s.website, '公式', '公式サイトを開く'));
    p.appendChild(
      button(
        'https://www.openstreetmap.org/?mlat=' + s.lat + '&mlon=' + s.lng + '#map=17/' + s.lat + '/' + s.lng,
        '地図',
        '地図で位置を確認する'
      )
    );
    node.appendChild(p);
    return node;
  }

  function button(href, text, tip) {
    const a = link(href, text);
    a.className = 'btn';
    if (tip) a.title = tip;
    return a;
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

  function cell(label, value, muted, tip) {
    const c = el('div', 'cell');
    c.appendChild(el('dt', null, label));
    const dd = el('dd', muted ? 'muted' : null, value);
    if (tip) dd.title = tip;
    c.appendChild(dd);
    return c;
  }

  boot();
})();
