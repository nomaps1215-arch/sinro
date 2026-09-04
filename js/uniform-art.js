/*
 * uniform-art.js — 制服の種類に応じた図案（自作のSVG）
 *
 * 実際の制服写真は学校サイトや制服店の著作物なので、取得して同梱することはできない。
 * ここでは制服の「種類」だけを表す、権利関係のない簡略図を描いている。
 *
 * 実写を使いたい場合は、各学校から許諾を得たうえで
 *   assets/uniforms/<school-id>.jpg
 * に置き、data/schools.json の該当レコードに
 *   "uniformImage": "assets/uniforms/<school-id>.jpg"
 * を足せば、この図案の代わりに表示される（js/app.js を参照）。
 */
const HSUniformArt = (function () {
  'use strict';

  // 上半身のシルエット（肩から裾まで）。どの種類でも共通の土台にする。
  const TORSO =
    '<path d="M35 15 L14 27 C11 29 10 32 10 36 L10 113 C10 116 12 118 15 118 ' +
    'L85 118 C88 118 90 116 90 113 L90 36 C90 32 89 29 86 27 L65 15 Z"/>';

  const PARTS = {
    // ブレザー：襟（ラペル）とネクタイ
    blazer:
      TORSO +
      '<path d="M35 15 L50 52 L65 15 L57 13 L50 34 L43 13 Z" opacity=".55"/>' +
      '<path d="M50 40 L44 47 L50 78 L56 47 Z" opacity=".8"/>' +
      '<circle cx="50" cy="92" r="2.4" opacity=".7"/>' +
      '<circle cx="50" cy="104" r="2.4" opacity=".7"/>',

    // セーラー服：背中側の大きな四角い襟と胸元のスカーフ
    sailor:
      TORSO +
      '<path d="M35 15 L50 55 L65 15 L60 13 L50 36 L40 13 Z" opacity=".55"/>' +
      '<path d="M33 16 L67 16 L64 40 L36 40 Z" opacity=".35"/>' +
      '<path d="M50 44 L42 52 L50 72 L58 52 Z" opacity=".8"/>',

    // 学ラン（詰襟）：立ち襟と前立ての釦
    gakuran:
      TORSO +
      '<path d="M37 13 L63 13 C65 13 66 15 66 17 L66 24 L34 24 L34 17 C34 15 35 13 37 13 Z" opacity=".6"/>' +
      '<path d="M49 24 L51 24 L51 118 L49 118 Z" opacity=".45"/>' +
      '<circle cx="45" cy="38" r="2.2" opacity=".7"/>' +
      '<circle cx="45" cy="56" r="2.2" opacity=".7"/>' +
      '<circle cx="45" cy="74" r="2.2" opacity=".7"/>',

    // 標準服：装飾の少ない前開きの上着
    standard:
      TORSO +
      '<path d="M35 15 L50 46 L65 15 L58 13 L50 30 L42 13 Z" opacity=".45"/>' +
      '<path d="M49 46 L51 46 L51 118 L49 118 Z" opacity=".4"/>' +
      '<circle cx="45" cy="62" r="2.2" opacity=".65"/>' +
      '<circle cx="45" cy="80" r="2.2" opacity=".65"/>',

    // 私服：フードとドローコード
    casual:
      TORSO +
      '<path d="M36 14 C40 26 60 26 64 14 L60 12 C57 20 43 20 40 12 Z" opacity=".5"/>' +
      '<path d="M44 22 L45 46" stroke-width="2.5" opacity=".7"/>' +
      '<path d="M56 22 L55 46" stroke-width="2.5" opacity=".7"/>'
  };

  // data/schools.json の uniform.type に入りうる表記の対応表
  const ALIASES = [
    [/ブレザー/, 'blazer'],
    [/セーラー/, 'sailor'],
    [/学ラン|詰襟|つめえり/, 'gakuran'],
    [/私服|制服なし|自由/, 'casual'],
    [/標準服|制服/, 'standard']
  ];

  function kindOf(type) {
    if (!type) return null;
    for (const [re, kind] of ALIASES) {
      if (re.test(type)) return kind;
    }
    return 'standard';
  }

  /** 制服の種類から SVG 文字列を返す。判定できないときは null。 */
  function svgFor(type) {
    const kind = kindOf(type);
    if (!kind) return null;
    return (
      '<svg viewBox="0 0 100 130" xmlns="http://www.w3.org/2000/svg" ' +
      'fill="currentColor" stroke="currentColor" stroke-width="0" aria-hidden="true" focusable="false">' +
      PARTS[kind] +
      '</svg>'
    );
  }

  return { svgFor, kindOf };
})();

if (typeof module !== 'undefined' && module.exports) module.exports = HSUniformArt;
