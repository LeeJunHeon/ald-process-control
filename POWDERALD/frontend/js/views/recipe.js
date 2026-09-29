/* ============================================================
 * views/recipe.js — 레시피 탭 (2단계 예정).
 *
 * 이번 단계에서는 안내만 둔다. 주소 정의(D02000~D03119)는 이미 들어 있으므로
 * 2단계에서 편집·검증·PLC 표 전송을 이 자리에 붙인다.
 * ★ 옛 레시피 모델·화면·샘플은 지웠다 — PLC 표 구조와 맞지 않아 그대로 두면
 *   "화면에는 있는데 장비는 모르는" 레시피가 생긴다.
 * ============================================================ */
(function (w, d) {
  'use strict';
  function render() { /* 정적 안내문 — 다시 그릴 것이 없다 */ }
  function update() { /* 실시간 값 없음 */ }
  core.register('recipe', { render: render, update: update });
  w.viewRecipe = { render: render, update: update };
})(window, document);
