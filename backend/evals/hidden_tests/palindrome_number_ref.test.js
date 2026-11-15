// Hidden reference tests, written by hand. They accept any exported function name.
const test = require('node:test');
const assert = require('node:assert/strict');
const mod = require('./solution');
const fns = (typeof mod === 'function' ? [mod] : Object.values(mod)).filter((v) => typeof v === 'function');
const f = fns.find((fn) => /palin/i.test(fn.name)) || fns[0];

test('palindromes', () => { for (const n of [0, 7, 11, 121, 12321]) assert.equal(f(n), true); });
test('not palindromes', () => { for (const n of [10, 123, 1231]) assert.equal(f(n), false); });
test('negative numbers', () => { assert.equal(f(-121), false); });
