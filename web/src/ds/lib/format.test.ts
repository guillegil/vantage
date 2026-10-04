import { describe, expect, it } from 'vitest';
import { capital, fmtBytes, fmtEarlier, fmtNumber, fmtValue } from './format';

describe('fmtValue', () => {
  it('prints a value as its format says, with its unit', () => {
    expect(fmtValue(12345.6, 'count')).toBe('12,346');
    expect(fmtValue(4.2, 'seconds')).toBe('4.20 s');
    expect(fmtValue(1536, 'bytes')).toBe('1.5 KiB');
    expect(fmtValue(97, 'percent')).toBe('97%');
    expect(fmtValue(97.25, 'percent')).toBe('97.3%');
    expect(fmtValue(3.29812345, 'number', 'V')).toBe('3.2981 V');
    expect(fmtValue(3.3, 'number', 'V', 3)).toBe('3.300 V');
    expect(fmtValue(7, (v) => `#${v}`)).toBe('#7');
    expect(fmtValue('main', 'text')).toBe('main');
    expect(fmtValue('main')).toBe('main');
  });

  it('prints a dash where there is no value', () => {
    for (const v of [null, undefined, '', Number.NaN, Number.POSITIVE_INFINITY])
      expect(fmtValue(v, 'number')).toBe('—');
  });
});

describe('fmtBytes and fmtNumber', () => {
  it('prints bytes the IEC way', () => {
    expect(fmtBytes(512)).toBe('512 B');
    expect(fmtBytes(12 * 1024)).toBe('12 KiB');
    expect(fmtBytes(184 * 1024 * 1024)).toBe('184 MiB');
    expect(fmtBytes(-1)).toBe('—');
  });

  it('keeps a number’s precision up to four decimals, grouped', () => {
    expect(fmtNumber(1234567.123456)).toBe('1,234,567.1235');
    expect(fmtNumber(2)).toBe('2');
    expect(fmtNumber(null)).toBe('—');
  });
});

describe('fmtEarlier', () => {
  it('says how much earlier in the words a run list uses for times', () => {
    expect(fmtEarlier(42)).toBe('42 s');
    expect(fmtEarlier(59.9)).toBe('59 s');
    expect(fmtEarlier(60)).toBe('1 min');
    expect(fmtEarlier(2520)).toBe('42 min');
    expect(fmtEarlier(10840)).toBe('3 h');
    expect(fmtEarlier(86399)).toBe('23 h');
    expect(fmtEarlier(190000)).toBe('2 d');
    expect(fmtEarlier(1500 * 86400)).toBe('1,500 d');
  });

  it('says under 1 s for less than a second, and for no value', () => {
    for (const v of [0.4, 0, -3, null, undefined, Number.NaN, Number.POSITIVE_INFINITY])
      expect(fmtEarlier(v)).toBe('under 1 s');
  });
});

it('capitalises the first letter alone', () => {
  expect(capital('new failure')).toBe('New failure');
  expect(capital('')).toBe('');
});
