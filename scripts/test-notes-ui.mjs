import assert from 'node:assert/strict';
import { moveInsertionOffset, formatSaveCountdown, notesErrorMessage, newOperationId } from '../assets/js/notes-api.js';

// Upload anchors must remain attached to the original paragraph across edits.
assert.equal(moveInsertionOffset('alpha\nbeta', 'NEW alpha\nbeta', 6), 10);
assert.equal(moveInsertionOffset('alpha\nbeta', 'alpha\nbeta END', 3), 3);
assert.equal(moveInsertionOffset('alpha\nbeta', 'a\nbeta', 6), 2);
assert.equal(moveInsertionOffset('alpha\nbeta', 'replacement', 3), 'replacement'.length);
assert.equal(moveInsertionOffset('中文\n记录', '前缀中文\n记录', 3), 5);
assert.equal(moveInsertionOffset('abc', 'abc', 1), 1);
assert.equal(formatSaveCountdown(60000), '01:00');
assert.equal(formatSaveCountdown(59999), '01:00');
assert.equal(formatSaveCountdown(-1), '00:00');
assert.equal(formatSaveCountdown(3600000), '60:00');
assert.match(newOperationId(), /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
assert.match(notesErrorMessage({ error: 'revision_conflict', message: 'internal server details' }, 409), /版本冲突/);
assert.doesNotMatch(notesErrorMessage({ error: 'unknown', message: 'D:\\private\\sensitive-path' }, 500), /private/);
console.log('Notes UI helpers: 13 assertions passed.');
