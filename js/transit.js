/*
 * transit.js — 通学時間の概算エンジン（オフライン）
 *
 * 外部APIを一切使わず、data/lines.json の駅座標と路線順序だけで
 * 「最寄り駅 → 高校」の所要時間を推定する。
 *
 * 精度の前提:
 *   - 駅間距離は直線距離 × RAIL_DETOUR で近似
 *   - 所要時間は路線ごとの表定速度 avgSpeedKmh から算出
 *   - 乗換は「駅どうしが TRANSFER_RADIUS_M 以内」で自動生成
 *   誤差はおおむね ±10分。正確な時刻表検索の代替にはならない。
 *
 * あとで有料の経路検索APIに差し替えられるよう、公開APIは route() ひとつに絞っている。
 */
const HSTransit = (function () {
  'use strict';

  // ---- 速度・係数のチューニング値 -------------------------------------
  const P = {
    WALK_MPM: 80,         // 徒歩 80m/分（分速80m = 時速4.8km）
    WALK_DETOUR: 1.30,    // 直線距離 → 実際の道路距離の補正
    BIKE_MPM: 240,        // 自転車 240m/分（時速14.4km）
    BIKE_DETOUR: 1.25,
    BUS_MPM: 250,         // 路線バス 時速15km 相当
    BUS_DETOUR: 1.35,
    BUS_WAIT_MIN: 7,      // バス待ち時間
    BUS_ACCESS_MIN: 4,    // 自宅→バス停 ＋ 降車バス停→学校 の徒歩
    RAIL_DETOUR: 1.10,    // 鉄道の線形補正
    // avgSpeedKmh は急行・快速を含む実測ダイヤに合わせて較正済みなので、
    // 停車時間は「各駅停車しか止まらない駅を使う場合」の上乗せ分だけを見る。
    DWELL_MIN: 0.2,
    TRANSFER_RADIUS_M: 500,
    TRANSFER_MIN_MIN: 3,  // 乗換の最低所要（改札移動など）
    THROUGH_MIN: 2,       // 直通運転がある路線間（乗り換えずに乗り通せることが多い）
    ACCESS_WALK_MAX_M: 1600,  // 自宅から歩いて乗れる駅の上限
    ACCESS_BIKE_MAX_M: 4000,  // 自転車で行ける駅の上限
    EGRESS_WALK_MAX_M: 2000   // 降車駅から学校まで歩ける上限
  };

  // ---- 距離計算 --------------------------------------------------------
  function haversineM(a, b) {
    const R = 6371000;
    const toRad = (d) => (d * Math.PI) / 180;
    const dLat = toRad(b.lat - a.lat);
    const dLng = toRad(b.lng - a.lng);
    const la1 = toRad(a.lat);
    const la2 = toRad(b.lat);
    const h =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(la1) * Math.cos(la2) * Math.sin(dLng / 2) ** 2;
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  const walkMin = (m) => (m * P.WALK_DETOUR) / P.WALK_MPM;
  const bikeMin = (m) => (m * P.BIKE_DETOUR) / P.BIKE_MPM;
  const busMin = (m) => (m * P.BUS_DETOUR) / P.BUS_MPM;

  // ---- グラフ構築 ------------------------------------------------------
  // node.key = "<lineId>#<index>"（環状線のように同名駅が2回出る路線があるため index を使う）
  let G = null;

  function build(lines) {
    const nodes = [];
    const byKey = new Map();

    lines.forEach((line) => {
      line.stations.forEach((st, i) => {
        const node = {
          key: line.id + '#' + i,
          idx: nodes.length,
          lineId: line.id,
          lineName: line.name,
          lineColor: line.color || '#888',
          waitMin: line.waitMin != null ? line.waitMin : 4,
          throughTo: line.throughTo || [],
          name: st.name,
          lat: st.lat,
          lng: st.lng
        };
        nodes.push(node);
        byKey.set(node.key, node);
      });
    });

    const adj = nodes.map(() => []);
    const addEdge = (a, b, min, kind) => {
      adj[a].push({ to: b, min, kind });
    };

    // 路線内の隣接駅
    lines.forEach((line) => {
      const speed = line.avgSpeedKmh || 40;
      for (let i = 0; i < line.stations.length - 1; i++) {
        const a = byKey.get(line.id + '#' + i);
        const b = byKey.get(line.id + '#' + (i + 1));
        const m = haversineM(a, b) * P.RAIL_DETOUR;
        const min = (m / 1000 / speed) * 60 + P.DWELL_MIN;
        addEdge(a.idx, b.idx, min, 'ride');
        addEdge(b.idx, a.idx, min, 'ride');
      }
    });

    // 乗換（近接駅を自動接続）
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i];
        const b = nodes[j];
        if (a.lineId === b.lineId && Math.abs(a.idx - b.idx) === 1) continue;
        const d = haversineM(a, b);
        if (d > P.TRANSFER_RADIUS_M) continue;
        if (a.lineId === b.lineId) {
          // 同一路線内の同一駅（環状線の起終点など）
          addEdge(a.idx, b.idx, 0, 'same');
          addEdge(b.idx, a.idx, 0, 'same');
          continue;
        }
        // 直通運転がある路線どうしは、実際には乗り換えずに乗り通せることが多い
        if (a.throughTo.includes(b.lineId) || b.throughTo.includes(a.lineId)) {
          addEdge(a.idx, b.idx, P.THROUGH_MIN, 'through');
          addEdge(b.idx, a.idx, P.THROUGH_MIN, 'through');
          continue;
        }
        const w = Math.max(P.TRANSFER_MIN_MIN, walkMin(d));
        addEdge(a.idx, b.idx, w + b.waitMin, 'transfer');
        addEdge(b.idx, a.idx, w + a.waitMin, 'transfer');
      }
    }

    G = { nodes, adj, byKey };
    return G;
  }

  function graph() {
    return G;
  }

  /** 駅名からノード一覧を引く（同名の別路線ノードをすべて返す） */
  function findStations(name) {
    if (!G) return [];
    return G.nodes.filter((n) => n.name === name);
  }

  /** ある地点の最寄り駅（学校カードの「最寄り駅 徒歩◯分」表示に使う） */
  function nearestStation(point) {
    if (!G) return null;
    let best = null;
    let bestD = Infinity;
    G.nodes.forEach((n) => {
      const d = haversineM(point, n);
      if (d < bestD) {
        bestD = d;
        best = n;
      }
    });
    if (!best) return null;
    return {
      name: best.name,
      lineName: best.lineName,
      meters: bestD,
      walkMin: walkMin(bestD)
    };
  }

  /** UI の駅プルダウン用。同名駅は1件にまとめ、路線名を添える。 */
  function stationList() {
    if (!G) return [];
    const map = new Map();
    G.nodes.forEach((n) => {
      const e = map.get(n.name);
      if (e) {
        if (!e.lines.includes(n.lineName)) e.lines.push(n.lineName);
      } else {
        map.set(n.name, { name: n.name, lat: n.lat, lng: n.lng, lines: [n.lineName] });
      }
    });
    return Array.from(map.values()).sort((a, b) => a.name.localeCompare(b.name, 'ja'));
  }

  // ---- ダイクストラ（仮想始点 → 仮想終点） ------------------------------
  function railRoute(origin, dest, opts) {
    const accessMode = opts.stationAccess === 'bike' ? 'bike' : 'walk';
    const accessMax = accessMode === 'bike' ? P.ACCESS_BIKE_MAX_M : P.ACCESS_WALK_MAX_M;
    const accessMin = accessMode === 'bike' ? bikeMin : walkMin;

    const n = G.nodes.length;
    const dist = new Float64Array(n).fill(Infinity);
    const prev = new Array(n).fill(null);
    const visited = new Uint8Array(n);
    const accessM = new Float64Array(n).fill(NaN);

    let seeded = false;
    G.nodes.forEach((node, i) => {
      const d = haversineM(origin, node);
      if (d > accessMax) return;
      const t = accessMin(d) + node.waitMin;
      if (t < dist[i]) {
        dist[i] = t;
        accessM[i] = d;
        prev[i] = { from: -1, kind: 'access' };
        seeded = true;
      }
    });
    if (!seeded) return null;

    // ノード数が数百なので単純な線形選択で十分速い
    for (;;) {
      let u = -1;
      let best = Infinity;
      for (let i = 0; i < n; i++) {
        if (!visited[i] && dist[i] < best) {
          best = dist[i];
          u = i;
        }
      }
      if (u === -1) break;
      visited[u] = 1;
      for (const e of G.adj[u]) {
        const nd = dist[u] + e.min;
        if (nd < dist[e.to]) {
          dist[e.to] = nd;
          prev[e.to] = { from: u, kind: e.kind };
        }
      }
    }

    // 降車駅の選択
    let endIdx = -1;
    let endTotal = Infinity;
    let endWalk = 0;
    for (let i = 0; i < n; i++) {
      if (!isFinite(dist[i])) continue;
      const d = haversineM(G.nodes[i], dest);
      if (d > P.EGRESS_WALK_MAX_M) continue;
      const t = dist[i] + walkMin(d);
      if (t < endTotal) {
        endTotal = t;
        endIdx = i;
        endWalk = d;
      }
    }
    if (endIdx === -1) return null;

    // 経路復元
    const chain = [];
    for (let i = endIdx; i !== -1 && i != null; ) {
      chain.push(i);
      const p = prev[i];
      if (!p || p.from === -1) break;
      i = p.from;
    }
    chain.reverse();

    const startIdx = chain[0];
    const legs = [];
    // 乗車駅そのものから乗る場合（ほとんどの場合）は移動が無いので、行を出さない。
    // 別の駅まで歩く場合だけ「どこからどこへ」を書く。
    if (accessM[startIdx] > 60) {
      const from = opts.originName ? opts.originName + '駅' : '出発地';
      legs.push({
        kind: accessMode,
        minutes: accessMin(accessM[startIdx]),
        label:
          (accessMode === 'bike' ? '自転車' : '徒歩') +
          '：' + from + ' → ' + G.nodes[startIdx].name + '駅',
        meters: accessM[startIdx]
      });
    }

    // 連続する同一路線の区間を1レグにまとめる
    let segStart = 0;
    for (let k = 1; k <= chain.length; k++) {
      const cur = chain[k];
      const prevNode = G.nodes[chain[k - 1]];
      const isEnd = k === chain.length;
      const kind = isEnd ? null : prev[cur].kind;
      if (isEnd || kind === 'transfer' || kind === 'through') {
        const a = G.nodes[chain[segStart]];
        const b = prevNode;
        if (a.idx !== b.idx) {
          let ride = 0;
          for (let m = segStart; m < k - 1; m++) {
            const from = chain[m];
            const to = chain[m + 1];
            const edge = G.adj[from].find((e) => e.to === to);
            if (edge) ride += edge.min;
          }
          legs.push({
            kind: 'train',
            minutes: ride,
            lineName: a.lineName,
            lineColor: a.lineColor,
            from: a.name,
            to: b.name,
            label: a.lineName + '：' + a.name + ' → ' + b.name
          });
        }
        if (!isEnd) {
          const t = G.adj[prevNode.idx].find((e) => e.to === cur);
          legs.push({
            kind: kind,
            minutes: t ? t.min : P.TRANSFER_MIN_MIN,
            label:
              (kind === 'through' ? '直通：' : '乗換：') +
              prevNode.name + ' → ' + G.nodes[cur].lineName
          });
          segStart = k;
        }
      }
    }

    legs.push({
      kind: 'walk',
      minutes: walkMin(endWalk),
      label: '徒歩：' + G.nodes[endIdx].name + '駅 → 学校',
      meters: endWalk
    });

    // 一度も列車に乗っていない（歩いた方が早い）場合は電車の選択肢として扱わない
    if (!legs.some((l) => l.kind === 'train')) return null;

    const transfers = legs.filter((l) => l.kind === 'transfer').length;
    return {
      mode: 'train',
      minutes: endTotal,
      transfers,
      fromStation: G.nodes[startIdx].name,
      toStation: G.nodes[endIdx].name,
      legs
    };
  }

  /**
   * 出発地（最寄り駅の座標など）から学校までの所要時間を、手段ごとに算出する。
   *
   * origin は「乗車駅」の座標を渡す前提。originName を添えると、
   * 経路の内訳に駅名が出る。
   *
   * @param {{lat:number,lng:number}} origin
   * @param {{lat:number,lng:number}} dest
   * @param {{modes?:string[], stationAccess?:'walk'|'bike', originName?:string}} [opts]
   * @returns {{best:object|null, byMode:Object}}
   */
  function route(origin, dest, opts) {
    opts = opts || {};
    const modes = opts.modes || ['walk', 'bike', 'bus', 'train'];
    const straight = haversineM(origin, dest);
    const byMode = {};

    if (modes.includes('walk')) {
      byMode.walk = {
        mode: 'walk',
        minutes: walkMin(straight),
        meters: straight * P.WALK_DETOUR,
        legs: [{ kind: 'walk', minutes: walkMin(straight), label: '徒歩のみ' }]
      };
    }
    if (modes.includes('bike')) {
      byMode.bike = {
        mode: 'bike',
        minutes: bikeMin(straight),
        meters: straight * P.BIKE_DETOUR,
        legs: [{ kind: 'bike', minutes: bikeMin(straight), label: '自転車のみ' }]
      };
    }
    if (modes.includes('bus')) {
      // バス路線データを持たないため「直線距離ベースの概算」であることを明示する
      const ride = busMin(straight);
      byMode.bus = {
        mode: 'bus',
        minutes: ride + P.BUS_WAIT_MIN + P.BUS_ACCESS_MIN,
        approx: true,
        legs: [
          { kind: 'walk', minutes: P.BUS_ACCESS_MIN, label: '徒歩：バス停まで／バス停から' },
          { kind: 'bus', minutes: ride + P.BUS_WAIT_MIN, label: 'バス（待ち時間込みの概算）' }
        ]
      };
    }
    if (modes.includes('train') && G) {
      const r = railRoute(origin, dest, opts);
      if (r) byMode.train = r;
    }

    let best = null;
    Object.values(byMode).forEach((r) => {
      if (r && (!best || r.minutes < best.minutes)) best = r;
    });
    return { best, byMode, straightMeters: straight };
  }

  return {
    build, graph, route, findStations, stationList, nearestStation, haversineM, params: P
  };
})();

if (typeof module !== 'undefined' && module.exports) module.exports = HSTransit;
