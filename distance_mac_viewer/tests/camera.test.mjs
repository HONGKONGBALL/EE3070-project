import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cameraOrigin } from '../camera-panel.js';

test('uses the Pi dashboard port when only an address is given', () => {
  assert.equal(cameraOrigin(' 192.168.0.161 '), 'http://192.168.0.161:8011');
});

test('keeps an explicit port and accepts a pasted dashboard URL', () => {
  assert.equal(cameraOrigin('http://Pi5.local:8000/'), 'http://pi5.local:8000');
});

test('rejects input that is not a plain host', () => {
  for (const bad of ['', 'https://pi.local', 'pi.local/stream', 'a b', 'pi.local:0', 'pi.local:70000', 'user@pi.local', '-pi'])
    assert.equal(cameraOrigin(bad), null, bad);
});
