import assert from 'node:assert/strict';
import test from 'node:test';

import { csvCell } from '../pi/cane/web/static/dashboard-replay.js';
import { display, number, yesNo } from '../pi/cane/web/static/dashboard-dom.js';
import * as dom from '../pi/cane/web/static/dashboard-dom.js';
import * as renderer from '../pi/cane/web/static/dashboard-render.js';

test('monocular camera does not enable a calibrated stereo scan', () => {
  // Given a live monocular camera without stereo capability.
  const status = { camera: { ready: true }, capabilities: { slope: false, calibrated_stereo: false } };
  // When scan availability is evaluated.
  const available = renderer.canScan(status);
  // Then depth-dependent scanning stays disabled.
  assert.equal(available, false);
});

test('calibrated stereo enables scanning independently of monocular preview', () => {
  // Given calibrated stereo, with no monocular preview.
  const status = { camera: { ready: false }, capabilities: { calibrated_stereo: true } };
  // When scan availability is evaluated.
  const available = renderer.canScan(status);
  // Then scanning is available.
  assert.equal(available, true);
});

test('absent capabilities keep scanning unavailable', () => {
  // Given a host with no advertised stereo capabilities.
  const status = { sim: true };
  // When scan availability is evaluated.
  const available = renderer.canScan(status);
  // Then simulated mode alone does not claim a real scan.
  assert.equal(available, false);
});

test('CSV export neutralizes formulas and quotes untrusted strings', () => {
  // Given a log event with spreadsheet formula syntax and quotes.
  const input = '=HYPERLINK("https://example.com")';
  // When its CSV field is created.
  const field = csvCell(input);
  // Then it remains literal text in spreadsheet software.
  assert.equal(field, '"\'=HYPERLINK(""https://example.com"")"');
});

test('invalid measurements and missing booleans display as unknown', () => {
  // Given absent and nonfinite observations.
  const input = [null, undefined, Number.NaN, Number.POSITIVE_INFINITY];
  // When values are rendered.
  const values = input.map((value) => number(value, 2));
  // Then no invalid value becomes a usable distance.
  assert.deepEqual(values, ['—', '—', '—', '—']);
  assert.equal(yesNo(undefined), '未知');
  assert.equal(display(null), '—');
});

test('scenario response name is not described as a matched destination', () => {
  // Given a named scenario response from the simulation endpoint.
  const response = { accepted: true, name: 'obstacle' };
  // When its user-facing result is formatted.
  const result = dom.responseFeedback('/api/sim/scenario', response, '模拟场景');
  // Then the message asks the user to verify observations without destination confirmation.
  assert.equal(result.error, false);
  assert.match(result.message, /模拟场景已送交主机/);
  assert.doesNotMatch(result.message, /已匹配|确认.*继续/);
});

test('destination response requires the user to check and confirm the match', () => {
  // Given a matched destination response.
  const response = { matched: 'cityu', name: '城大' };
  // When its user-facing result is formatted.
  const result = dom.responseFeedback('/api/voice/text', response, '目的地识别');
  // Then the destination confirmation workflow is retained.
  assert.match(result.message, /已匹配“城大”/);
  assert.match(result.message, /确认/);
});

test('destination choice belongs to the current search and known candidate', () => {
  const voice = {
    search_state: 'results', search_id: 'current',
    candidates: [{ id: 'north', name: '城大北门', address: '达之路' }],
  };
  assert.deepEqual(renderer.voiceSelection(voice, 'current', 'north'), {
    search_id: 'current', candidate_id: 'north',
  });
  assert.equal(renderer.voiceSelection(voice, 'previous', 'north'), null);
  assert.equal(renderer.voiceSelection(voice, 'current', 'unknown'), null);
});

test('the voice bench shows the active search origin or offers a choice', () => {
  assert.equal(renderer.searchOriginText({ search_origin: { kind: 'iphone' } }), '出发点 · iPhone 当前位置');
  assert.equal(renderer.searchOriginText({ search_origin: { kind: 'manual', name: '城大北门' } }), '出发点 · 城大北门');
  assert.equal(renderer.searchOriginText({ search_origin: null }), '要用 iPhone 当前位置，还是改用一个地点？');
});

test('a spoken origin search opens the origin editor and preserves the destination choices', (t) => {
  class Element {
    dataset = {};
    children = [];
    append(child) { this.children.push(child); }
    replaceChildren(...children) { this.children = children; }
    querySelectorAll() { return this.children.map((row) => row.children[0]); }
  }
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  globalThis.document = { getElementById: element, createElement: () => new Element() };
  t.after(() => { delete globalThis.document; });

  const destination = {
    search_id: 'destination', search_state: 'results', candidates: [{ id: 'gym', name: '健身房' }],
  };
  const origin = {
    search_id: 'origin', search_state: 'results', candidates: [{ id: 'north', name: '城大北门', address: '达之路' }],
  };

  renderer.renderVoiceSearch(destination, false);
  const [destinationButton] = element('destination-candidates').querySelectorAll();
  renderer.renderOriginSearch({ origin_search: origin }, false);

  assert.equal(element('origin-editor').open, true);
  const [originButton] = element('origin-candidates').querySelectorAll();
  assert.equal(originButton.children[1].textContent, '达之路');
  assert.deepEqual(renderer.voiceSelection(origin, originButton.dataset.searchId, originButton.dataset.candidateId), {
    search_id: 'origin', candidate_id: 'north',
  });
  assert.equal(renderer.voiceSelection(destination, originButton.dataset.searchId, originButton.dataset.candidateId), null);

  element('origin-editor').open = false;
  renderer.renderOriginSearch({ origin_search: origin }, true);
  assert.equal(element('origin-editor').open, false);
  assert.equal(originButton.disabled, true);
  assert.equal(element('destination-candidates').querySelectorAll()[0], destinationButton);
});

test('pending search prevents an old destination confirmation while keeping crossing confirmation', () => {
  const voice = { search_state: 'results' };
  assert.ok(renderer.controlDisabledReason('confirm', { voice, nav: { pending_destination: '旧地点' } }));
  assert.equal(renderer.controlDisabledReason('confirm', { voice, nav: { waiting_crossing: true } }), '');
  assert.equal(renderer.controlDisabledReason('confirm', { voice: { search_state: 'selected' } }), '');
});

test('slope direction is named from the fitted angle sign', () => {
  // Given positive uphill and negative sideways descent observations.
  const angles = [12, -8, null];
  // When fitted slopes are formatted for the dashboard.
  const values = angles.map((angle) => renderer.slopeText(angle, '上坡', '下坡'));
  // Then direction and absence stay explicit.
  assert.deepEqual(values, ['上坡 12.0°', '下坡 8.0°', '—']);
});

test('host voice input is available only when the host advertises offline speech', () => {
  // Given hosts with an available model, missing model, or no voice capabilities.
  const statuses = [{ voice: { available: true } }, { voice: { available: false, reason: 'offline_model_missing' } }, {}];
  // When voice action availability is evaluated.
  const reasons = statuses.map((status) => renderer.controlDisabledReason('voice_input', status));
  // Then only the available offline recognizer can be started.
  assert.equal(reasons[0], '');
  assert.notEqual(reasons[1], '');
  assert.notEqual(reasons[2], '');
});

test('recording or Cantonese mode disables a second offline recognition request', () => {
  // Given an already recording host or Cantonese without a supported local model.
  const statuses = [{ voice: { available: true, recording: true } }, { language: 'yue', voice: { available: true, cantonese_model: false } }];
  // When voice action availability is evaluated.
  const reasons = statuses.map((status) => renderer.controlDisabledReason('voice_input', status));
  // Then the UI cannot request unsupported or concurrent recording.
  assert.ok(reasons.every((reason) => reason.length > 0));
});

test('a manual recording keeps the stop control enabled until requested', () => {
  assert.equal(renderer.controlDisabledReason('voice_stop', {
    voice: { available: true, recording: true, can_stop_recording: true },
  }), '');
  assert.notEqual(renderer.controlDisabledReason('voice_stop', {
    voice: { available: true, listening: true, can_stop_recording: false },
  }), '');
});

test('legacy and unknown real serial protocols disable physical output controls', () => {
  // Given real serial links without verified actuator command support.
  const statuses = [{ sim: false, serial: { controls_available: false } }, { sim: false, serial: {} }];
  // When the light action is evaluated.
  const reasons = statuses.map((status) => renderer.controlDisabledReason('light', status));
  // Then those clicks cannot be described as executable hardware controls.
  assert.ok(reasons.every((reason) => reason.includes('仅接收')));
});

test('JSON actuator support and explicit simulation permit their respective light controls', () => {
  // Given a command-capable real link and a simulated host.
  const statuses = [{ sim: false, serial: { controls_available: true } }, { sim: true }];
  // When the light action is evaluated.
  const reasons = statuses.map((status) => renderer.controlDisabledReason('light', status));
  // Then the supported controls stay available.
  assert.deepEqual(reasons, ['', '']);
});
