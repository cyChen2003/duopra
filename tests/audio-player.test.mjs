import assert from 'node:assert/strict';
import { test } from 'node:test';
import { AudioPlayer } from '../frontend/static/audio.js';

function fixture(bufferSeconds = 0) {
  const nodes = [], receipts = [], text = [];
  const context = {
    currentTime: 1,
    createBuffer(channels, frames, rate) {
      return { duration: frames / rate, getChannelData: () => new Float32Array(frames) };
    },
    createBufferSource() {
      const node = { connect() {}, disconnect() {},
        start(time, offset) { this.time = time; this.offset = offset; },
        stop() { this.stopped = true; } };
      nodes.push(node);
      return node;
    },
  };
  const player = new AudioPlayer({ bufferSeconds, maxWaitMs: 60000,
    ack: value => receipts.push(value), onText: value => text.push(value),
    onActivity() {}, onError(error) { throw error; } });
  player.context = context;
  player.prime = async () => {};
  const chunk = (id, seconds = 1) => ({ utterance_id: id, chunk_seq: 1, text: id,
    format: 'pcm_s16le', sample_rate: 24000, channels: 1,
    data: Buffer.alloc(Math.round(seconds * 24000) * 2).toString('base64') });
  return { player, context, nodes, receipts, text, chunk };
}

test('cancel removes queued audio and its timer', async () => {
  const { player, chunk } = fixture(2);
  player.enqueue(chunk('old'));
  await player.chain;
  assert.equal(player.pending.length, 1);
  assert.notEqual(player.bufferTimer, null);
  player.cancel(['old']);
  assert.equal(player.pending.length, 0);
  assert.equal(player.pendingSeconds, 0);
  assert.equal(player.bufferTimer, null);
  assert.equal(player.sources.size, 0);
});

test('cancel stops old sources and moves the new reply ahead of the discarded queue', async () => {
  const { player, context, nodes, receipts, chunk } = fixture();
  player.enqueue(chunk('old', 5));
  player.enqueue(chunk('new', 1));
  await player.chain;
  const [oldSource, originalNewSource] = nodes;
  context.currentTime = 2;
  player.cancel(['old']);
  assert.equal(oldSource.stopped, true);
  assert.equal(oldSource.onended, null);
  assert.equal(originalNewSource.stopped, true);
  assert.equal(player.sources.size, 1);
  const next = nodes.at(-1);
  assert.equal(next.time, 2.02);
  assert.equal(next.offset, 0);
  next.onended();
  assert.deepEqual(receipts.map(value => value.utterance_id), ['new']);
});

test('cancelled asynchronous decode cannot enqueue old audio after cancellation', async () => {
  const { player, context, nodes, text, chunk } = fixture();
  let release, decodeStarted;
  const decoding = new Promise(resolve => { decodeStarted = resolve; });
  context.decodeAudioData = () => { decodeStarted(); return new Promise(resolve => { release = resolve; }); };
  player.enqueue({ ...chunk('old'), format: 'wav' });
  await decoding;
  player.cancel(['old']);
  player.enqueue(chunk('new'));
  release({ duration: 1 });
  await player.chain;
  assert.deepEqual(text, ['new']);
  assert.equal(nodes.length, 1);
  assert.equal(player.sources.size, 1);
  player.clear();
});

test('a late cancellation for an already finished reply does not restart the new reply', async () => {
  const { player, nodes, chunk } = fixture();
  player.enqueue(chunk('new'));
  await player.chain;
  player.cancel(['old']);
  assert.equal(nodes.length, 1);
  assert.notEqual(nodes[0].stopped, true);
  player.clear();
});

test('rescheduling preserves the played offset of a newer reply', async () => {
  const { player, context, nodes, chunk } = fixture();
  player.enqueue(chunk('old', 5));
  player.enqueue(chunk('new', 2));
  await player.chain;
  // Audio has advanced before the old source's ended event is dispatched.
  context.currentTime = 6.42;
  player.cancel(['old']);
  const resumed = nodes.at(-1);
  assert.ok(Math.abs(resumed.offset - .4) < 1e-10);
  assert.ok(Math.abs(resumed.time - 6.44) < 1e-10);
  assert.ok(Math.abs(player.cursor - 8.04) < 1e-10);
  player.clear();
});

test('completed newer audio is acknowledged when cancellation wins the ended-event race', async () => {
  const { player, context, receipts, chunk } = fixture();
  player.enqueue(chunk('old', 1));
  player.enqueue(chunk('new', 1));
  await player.chain;
  context.currentTime = 4;
  player.cancel(['old']);
  assert.equal(player.sources.size, 0);
  assert.deepEqual(receipts.map(value => value.utterance_id), ['new']);
});

test('normal end drains a short tail; session clear invalidates all scheduled audio', async () => {
  const { player, nodes, chunk } = fixture(2);
  player.enqueue(chunk('normal', .1));
  player.finish('normal');
  await player.chain;
  assert.equal(nodes.length, 1);
  assert.notEqual(nodes[0].stopped, true);
  player.clear();
  assert.equal(nodes[0].stopped, true);
  assert.equal(player.sources.size, 0);
});
