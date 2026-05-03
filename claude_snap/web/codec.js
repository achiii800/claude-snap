// claude-snap codec — JS port of the Python unpack/pack logic.
// Pure module, no DOM, no I/O. Roundtrip-compatible with the Python reference.
// MIT.

export const SNAP_HEADER = 'snap_header';
export const SNAP_FOOTER = 'snap_footer';
export const SNAP_EVENT = 'snap_event';
export const SNAP_REF = 'snap_ref';
export const SNAP_DANGLING_REF = 'snap_dangling_ref';

/**
 * Parse a JSONL string into an array of objects.
 * Bad lines are skipped silently to match the Python reader's behavior.
 * @param {string} text
 * @returns {Array<object>}
 */
export function readJsonl(text) {
  const out = [];
  if (typeof text !== 'string') return out;
  const lines = text.split('\n');
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    try {
      out.push(JSON.parse(line));
    } catch (_e) {
      // Match Python codec: silently skip malformed lines.
    }
  }
  return out;
}

/**
 * Serialize an array of objects as JSONL.
 * @param {Array<object>} records
 * @returns {string}
 */
export function writeJsonl(records) {
  return records.map(r => JSON.stringify(r)).join('\n') + '\n';
}

/**
 * Detect whether a parsed-JSONL list is a packed snap stream.
 * @param {Array<object>} records
 */
export function isPacked(records) {
  if (!Array.isArray(records) || records.length === 0) return false;
  const head = records[0];
  return head && typeof head === 'object' && head.type === SNAP_HEADER;
}

/**
 * Resolve refs back to full payloads. Mirrors codec.py:unpack().
 *
 * Property: for any list of canonical Events `evs`,
 *   unpack(pack(evs)) === [ev.payload for ev in evs]
 * (modulo header/footer metadata).
 *
 * @param {Array<object>} packed - parsed snap_* records
 * @returns {Array<object>} restored raw JSONL events in original order
 */
export function unpack(packed) {
  const byHash = new Map();
  const out = [];
  if (!Array.isArray(packed)) return out;

  for (const x of packed) {
    if (!x || typeof x !== 'object') continue;
    const t = x.type;

    if (t === SNAP_HEADER || t === SNAP_FOOTER) continue;

    if (t === SNAP_EVENT) {
      byHash.set(x.content_hash, x.payload);
      out.push(x.payload);
      continue;
    }

    if (t === SNAP_REF) {
      const refTo = x.ref_to;
      const payload = byHash.get(refTo);
      if (payload === undefined) {
        out.push({
          type: SNAP_DANGLING_REF,
          ref_to: refTo,
          seq: x.seq,
        });
        continue;
      }

      // Deep-copy via JSON round-trip — payloads are JSON-safe.
      const restored = JSON.parse(JSON.stringify(payload));

      // Patch the inner tool_use_id so tool_use ↔ tool_result linkage stays correct.
      const newToolId = x.tool_id;
      if (newToolId && restored && typeof restored === 'object') {
        const msg = restored.message;
        if (msg && typeof msg === 'object') {
          const content = msg.content;
          if (Array.isArray(content)) {
            for (const b of content) {
              if (b && typeof b === 'object' && b.type === 'tool_result') {
                b.tool_use_id = newToolId;
              }
            }
          }
        }
      }

      // Overlay per-event metadata (uuid / parentUuid / timestamp / ...).
      const patch = x.patch;
      if (patch && typeof patch === 'object' && restored && typeof restored === 'object') {
        Object.assign(restored, patch);
      }

      out.push(restored);
    }
  }

  return out;
}

/**
 * Pull a flat list of {role, content} chat-style messages from a list of
 * raw Claude Code JSONL events, suitable for sending to Anthropic Messages API.
 *
 * - Skips meta/system/sidechain events.
 * - Concatenates assistant text blocks; drops thinking blocks (the API doesn't
 *   accept extended-thinking blocks as input from the messages array).
 * - Concatenates user text content; tool_results become text summaries.
 *
 * @param {Array<object>} events  raw events (unpacked or original)
 * @returns {Array<{role: string, content: string}>}
 */
export function eventsToMessages(events) {
  const msgs = [];
  if (!Array.isArray(events)) return msgs;

  for (const ev of events) {
    if (!ev || typeof ev !== 'object') continue;
    if (ev.isSidechain) continue;

    const t = ev.type;
    if (t !== 'user' && t !== 'assistant') continue;

    const inner = ev.message;
    if (!inner || typeof inner !== 'object') continue;

    const content = inner.content;
    let text = '';

    if (typeof content === 'string') {
      text = content;
    } else if (Array.isArray(content)) {
      const parts = [];
      for (const b of content) {
        if (!b || typeof b !== 'object') continue;
        switch (b.type) {
          case 'text':
            if (typeof b.text === 'string') parts.push(b.text);
            break;
          case 'thinking':
            // Drop thinking blocks — they don't replay through the API.
            break;
          case 'tool_use': {
            const name = b.name || 'tool';
            const input = b.input ? JSON.stringify(b.input) : '';
            parts.push(`[tool_use ${name}${input ? ` ${input}` : ''}]`);
            break;
          }
          case 'tool_result': {
            const inner2 = b.content;
            if (typeof inner2 === 'string') {
              parts.push(`[tool_result]\n${inner2}`);
            } else if (Array.isArray(inner2)) {
              const txt = inner2
                .filter(x => x && typeof x === 'object' && typeof x.text === 'string')
                .map(x => x.text)
                .join('\n');
              if (txt) parts.push(`[tool_result]\n${txt}`);
            }
            break;
          }
          default:
            break;
        }
      }
      text = parts.join('\n\n').trim();
    }

    if (!text) continue;

    const role = (t === 'user') ? 'user' : 'assistant';
    // Collapse adjacent same-role messages for API compatibility.
    if (msgs.length > 0 && msgs[msgs.length - 1].role === role) {
      msgs[msgs.length - 1].content += '\n\n' + text;
    } else {
      msgs.push({ role, content: text });
    }
  }

  return msgs;
}

/**
 * Surface stats on a packed stream — port of codec.py:stats().
 * @param {Array<object>} packed
 */
export function stats(packed) {
  let events = 0;
  let refs = 0;
  const eventBytesByHash = new Map();
  if (!Array.isArray(packed)) {
    return { events, refs, events_plus_refs: 0, bytes_unpacked: 0, bytes_packed: 0, compression_ratio: 1 };
  }
  for (const x of packed) {
    if (x && x.type === SNAP_EVENT) {
      eventBytesByHash.set(x.content_hash, JSON.stringify(x).length);
    }
  }
  let fullBytes = 0;
  let packedBytes = 0;
  for (const x of packed) {
    if (!x || typeof x !== 'object') continue;
    if (x.type === SNAP_EVENT) {
      events += 1;
      const b = JSON.stringify(x).length;
      fullBytes += b;
      packedBytes += b;
    } else if (x.type === SNAP_REF) {
      refs += 1;
      fullBytes += eventBytesByHash.get(x.ref_to) || 0;
      packedBytes += JSON.stringify(x).length;
    }
  }
  const ratio = fullBytes / Math.max(packedBytes, 1);
  return {
    events,
    refs,
    events_plus_refs: events + refs,
    bytes_unpacked: fullBytes,
    bytes_packed: packedBytes,
    compression_ratio: Math.round(ratio * 100) / 100,
  };
}
