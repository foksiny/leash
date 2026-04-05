/**
 * Leash Runtime for Browser/HTML
 * Browser-compatible system simulation using Web APIs.
 * 
 * ALL variables, arrays, structs, etc. are stored in simulated virtual memory.
 * Every variable is an address. Reading/writing goes through the memory system.
 * This makes pointer semantics natural and consistent.
 */

// ============================================================
// Virtual Memory System
// ============================================================
const _leash_memory = new Map();
let _leash_next_address = 0x1000;

function _leash_alloc(value, size = 8) {
    const addr = _leash_next_address;
    _leash_next_address += size;
    _leash_memory.set(addr, { value, size });
    return addr;
}

function _leash_load(addr) {
    const entry = _leash_memory.get(addr);
    if (!entry) return 0;
    return entry.value;
}

function _leash_store(addr, value) {
    const entry = _leash_memory.get(addr);
    if (entry) {
        entry.value = value;
    }
}

function _leash_ptr_str(addr) {
    return '0x' + addr.toString(16).padStart(12, '0');
}

function _leash_ptr_add(addr, offset) {
    return addr + offset;
}

function _leash_ptr_diff(addr1, addr2) {
    return addr1 - addr2;
}

// ============================================================
// Type System Emulation
// ============================================================

function _leash_int8(val) {
    return ((val & 0xFF) << 24) >> 24;
}

function _leash_uint8(val) {
    return val & 0xFF;
}

function _leash_int16(val) {
    return ((val & 0xFFFF) << 16) >> 16;
}

function _leash_uint16(val) {
    return val & 0xFFFF;
}

function _leash_int32(val) {
    return val | 0;
}

function _leash_uint32(val) {
    return val >>> 0;
}

function _leash_int64(val) {
    return BigInt(val);
}

function _leash_uint64(val) {
    return BigInt(val) & BigInt("0xFFFFFFFFFFFFFFFF");
}

function _leash_make_mask(bits) {
    return (BigInt(1) << BigInt(bits)) - BigInt(1);
}

function _leash_intN(val, bits) {
    const mask = _leash_make_mask(bits);
    const signed = BigInt(val) & mask;
    const half = BigInt(1) << BigInt(bits - 1);
    return signed >= half ? signed - (BigInt(1) << BigInt(bits)) : signed;
}

function _leash_uintN(val, bits) {
    const mask = _leash_make_mask(bits);
    return BigInt(val) & mask;
}

function _leash_add(a, b, type) {
    switch (type) {
        case 'int8': return _leash_int8(a + b);
        case 'uint8': return _leash_uint8(a + b);
        case 'int16': return _leash_int16(a + b);
        case 'uint16': return _leash_uint16(a + b);
        case 'int32': return _leash_int32(a + b);
        case 'uint32': return _leash_uint32(a + b);
        case 'int64': return _leash_int64(a) + _leash_int64(b);
        case 'uint64': return _leash_uint64(a) + _leash_uint64(b);
        case 'float': return a + b;
        default:
            if (type.startsWith('int')) {
                const bits = parseInt(type.substring(3));
                if (!isNaN(bits) && bits > 64) {
                    return _leash_intN(BigInt(a) + BigInt(b), bits);
                }
            }
            if (type.startsWith('uint')) {
                const bits = parseInt(type.substring(4));
                if (!isNaN(bits) && bits > 64) {
                    return _leash_uintN(BigInt(a) + BigInt(b), bits);
                }
            }
            return a + b;
    }
}

function _leash_sub(a, b, type) {
    switch (type) {
        case 'int8': return _leash_int8(a - b);
        case 'uint8': return _leash_uint8(a - b);
        case 'int16': return _leash_int16(a - b);
        case 'uint16': return _leash_uint16(a - b);
        case 'int32': return _leash_int32(a - b);
        case 'uint32': return _leash_uint32(a - b);
        case 'int64': return _leash_int64(a) - _leash_int64(b);
        case 'uint64': return _leash_uint64(a) - _leash_uint64(b);
        case 'float': return a - b;
        default:
            if (type.startsWith('int')) {
                const bits = parseInt(type.substring(3));
                if (!isNaN(bits) && bits > 64) {
                    return _leash_intN(BigInt(a) - BigInt(b), bits);
                }
            }
            if (type.startsWith('uint')) {
                const bits = parseInt(type.substring(4));
                if (!isNaN(bits) && bits > 64) {
                    return _leash_uintN(BigInt(a) - BigInt(b), bits);
                }
            }
            return a - b;
    }
}

function _leash_mul(a, b, type) {
    switch (type) {
        case 'int8': return _leash_int8(a * b);
        case 'uint8': return _leash_uint8(a * b);
        case 'int16': return _leash_int16(a * b);
        case 'uint16': return _leash_uint16(a * b);
        case 'int32': return _leash_int32(a * b);
        case 'uint32': return _leash_uint32(a * b);
        case 'int64': return _leash_int64(a) * _leash_int64(b);
        case 'uint64': return _leash_uint64(a) * _leash_uint64(b);
        case 'float': return a * b;
        default:
            if (type.startsWith('int')) {
                const bits = parseInt(type.substring(3));
                if (!isNaN(bits) && bits > 64) {
                    return _leash_intN(BigInt(a) * BigInt(b), bits);
                }
            }
            if (type.startsWith('uint')) {
                const bits = parseInt(type.substring(4));
                if (!isNaN(bits) && bits > 64) {
                    return _leash_uintN(BigInt(a) * BigInt(b), bits);
                }
            }
            return a * b;
    }
}

function _leash_div(a, b, type) {
    if (b === 0 || b === 0n) return 0;
    switch (type) {
        case 'int8': return _leash_int8(Math.trunc(a / b));
        case 'uint8': return _leash_uint8(Math.trunc(a / b));
        case 'int16': return _leash_int16(Math.trunc(a / b));
        case 'uint16': return _leash_uint16(Math.trunc(a / b));
        case 'int32': return _leash_int32(Math.trunc(a / b));
        case 'uint32': return _leash_uint32(Math.trunc(a / b));
        case 'int64': return _leash_int64(a) / _leash_int64(b);
        case 'uint64': return _leash_uint64(a) / _leash_uint64(b);
        case 'float': return a / b;
        default:
            if (type.startsWith('int') || type.startsWith('uint')) {
                const bits = parseInt(type.startsWith('int') ? type.substring(3) : type.substring(4));
                if (!isNaN(bits) && bits > 64) {
                    const ba = BigInt(a);
                    const bb = BigInt(b);
                    const result = ba / bb;
                    return type.startsWith('int') ? _leash_intN(result, bits) : _leash_uintN(result, bits);
                }
            }
            return Math.trunc(a / b);
    }
}

function _leash_mod(a, b, type) {
    if (b === 0 || b === 0n) return 0;
    switch (type) {
        case 'int8': return _leash_int8(a % b);
        case 'uint8': return _leash_uint8(a % b);
        case 'int16': return _leash_int16(a % b);
        case 'uint16': return _leash_uint16(a % b);
        case 'int32': return _leash_int32(a % b);
        case 'uint32': return _leash_uint32(a % b);
        case 'int64': return _leash_int64(a) % _leash_int64(b);
        case 'uint64': return _leash_uint64(a) % _leash_uint64(b);
        default:
            if (type.startsWith('int') || type.startsWith('uint')) {
                const bits = parseInt(type.startsWith('int') ? type.substring(3) : type.substring(4));
                if (!isNaN(bits) && bits > 64) {
                    const result = BigInt(a) % BigInt(b);
                    return type.startsWith('int') ? _leash_intN(result, bits) : _leash_uintN(result, bits);
                }
            }
            return a % b;
    }
}

function _leash_not(val, type) {
    switch (type) {
        case 'int8': return _leash_int8(~val);
        case 'uint8': return _leash_uint8(~val);
        case 'int16': return _leash_int16(~val);
        case 'uint16': return _leash_uint16(~val);
        case 'int32': return _leash_int32(~val);
        case 'uint32': return _leash_uint32(~val);
        default:
            if (type.startsWith('int') || type.startsWith('uint')) {
                const bits = parseInt(type.startsWith('int') ? type.substring(3) : type.substring(4));
                if (!isNaN(bits) && bits > 32) {
                    const mask = _leash_make_mask(bits);
                    return type.startsWith('int')
                        ? _leash_intN(BigInt(val) ^ mask, bits)
                        : _leash_uintN(BigInt(val) ^ mask, bits);
                }
            }
            return ~val;
    }
}

// ============================================================
// Display Formatting
// ============================================================

function _leash_display_int(val) {
    return String(typeof val === 'bigint' ? val : Math.trunc(val));
}

function _leash_display_uint(val) {
    if (typeof val === 'bigint') return String(val);
    return String(Math.trunc(val) >>> 0);
}

function _leash_display_int8(val) {
    const code = _leash_int8(val);
    if (code >= 32 && code <= 126) {
        return String.fromCharCode(code);
    }
    return String(code);
}

function _leash_display_uint8(val) {
    const code = _leash_uint8(val);
    if (code >= 32 && code <= 126) {
        return String.fromCharCode(code);
    }
    return String(code);
}

function _leash_display_int16(val) {
    return String(_leash_int16(val));
}

function _leash_display_uint16(val) {
    return String(_leash_uint16(val));
}

function _leash_display_float(val) {
    if (typeof val === 'number') {
        return val.toFixed(6);
    }
    return String(val);
}

function _leash_display_char(val) {
    if (typeof val === 'string' && val.length > 0) {
        return val[0];
    }
    if (typeof val === 'number') {
        if (val >= 32 && val <= 126) {
            return String.fromCharCode(val);
        }
        return String.fromCharCode(val & 0xFF);
    }
    return String(val);
}

function _leash_display_bool(val) {
    return val ? '1' : '0';
}

function _leash_display_string(val) {
    return String(val);
}

function _leash_display_char_array(addr) {
    const arr = _leash_load(addr);
    if (Array.isArray(arr)) {
        return String.fromCharCode(...arr);
    }
    return String(arr);
}

function _leash_display_array(addr) {
    const arr = _leash_load(addr);
    if (Array.isArray(arr)) {
        const isCharArray = arr.every(x => typeof x === 'number' && x >= 0 && x <= 255);
        if (isCharArray) {
            return _leash_display_char_array(addr);
        }
        return JSON.stringify(arr);
    }
    return String(arr);
}

function _leash_display_union(val) {
    if (val === null || val === undefined) return '';
    if (typeof val === 'object' && val._tag !== undefined) {
        switch (val._tag) {
            case 'i': return _leash_display_int(val._value);
            case 'u': return _leash_display_uint(val._value);
            case 'f': return _leash_display_float(val._value);
            case 's': return _leash_display_string(val._value);
            default: return String(val._value);
        }
    }
    return String(val);
}

function _leash_display(val) {
    if (val === null || val === undefined) return '';
    
    if (typeof val === 'object' && val._tag !== undefined) {
        return _leash_display_union(val);
    }
    
    if (Array.isArray(val)) {
        const isCharArray = val.every(x => typeof x === 'number' && x >= 0 && x <= 255);
        if (isCharArray) {
            return String.fromCharCode(...val);
        }
        return JSON.stringify(val);
    }
    
    if (typeof val === 'bigint') {
        return _leash_display_int(val);
    }
    
    if (typeof val === 'number' && val >= 0x1000 && val < _leash_next_address) {
        return _leash_ptr_str(val);
    }
    
    if (typeof val === 'number') {
        if (Number.isInteger(val)) {
            return _leash_display_int(val);
        }
        return _leash_display_float(val);
    }
    
    if (typeof val === 'boolean') {
        return _leash_display_bool(val);
    }
    
    return String(val);
}

function _leash_display_smart(val) {
    if (val === null || val === undefined) return '';
    
    if (typeof val === 'object' && val._tag !== undefined) {
        return _leash_display_union(val);
    }
    
    if (Array.isArray(val)) {
        const isCharArray = val.every(x => typeof x === 'number' && x >= 0 && x <= 255);
        if (isCharArray) {
            return String.fromCharCode(...val);
        }
        return JSON.stringify(val);
    }
    
    if (typeof val === 'bigint') {
        return _leash_display_int(val);
    }
    
    if (typeof val === 'number' && val >= 0x1000 && val < _leash_next_address) {
        return _leash_ptr_str(val);
    }
    
    if (typeof val === 'number') {
        if (Number.isInteger(val) && val >= 32 && val <= 126) {
            return String.fromCharCode(val);
        }
        if (Number.isInteger(val)) {
            return _leash_display_int(val);
        }
        return _leash_display_float(val);
    }
    
    if (typeof val === 'boolean') {
        return _leash_display_bool(val);
    }
    
    return String(val);
}

// ============================================================
// Random number generation
// ============================================================
let _leash_seed = Date.now() & 0xFFFFFFFF;

function _leash_seed_rand(s) {
    _leash_seed = s >>> 0;
}

function _leash_rand() {
    _leash_seed = (_leash_seed * 1103515245 + 12345) & 0x7FFFFFFF;
    let val = _leash_seed;
    if (arguments.length === 2) {
        const min = arguments[0];
        const max = arguments[1];
        const range = max - min + 1;
        val = min + (val % range);
    }
    return val;
}

function _leash_randf() {
    let val = _leash_seed / 0x7FFFFFFF;
    if (arguments.length === 2) {
        const min = arguments[0];
        const max = arguments[1];
        val = min + val * (max - min);
    }
    return val;
}

// ============================================================
// Timing
// ============================================================
const _leash_start_time = Date.now();

function _leash_timepass() {
    return (Date.now() - _leash_start_time) / 1000;
}

function _leash_wait(seconds) {
    return new Promise((resolve) => {
        setTimeout(resolve, seconds * 1000);
    });
}

// ============================================================
// String/Char functions
// ============================================================
function cstr(s) {
    if (typeof s === 'string') {
        const arr = [];
        for (let i = 0; i < s.length; i++) {
            arr.push(s.charCodeAt(i));
        }
        const addr = _leash_alloc(arr, 8);
        return addr;
    }
    return s;
}

function _leash_load_char(ptr_addr, offset) {
    const arr = _leash_load(ptr_addr);
    if (Array.isArray(arr) && offset < arr.length) {
        return arr[offset];
    }
    return 0;
}

function lstr(addr) {
    const arr = _leash_load(addr);
    if (Array.isArray(arr)) {
        return String.fromCharCode(...arr);
    }
    return String(arr);
}

function _leash_str_sub(str, sub) {
    const idx = str.indexOf(sub);
    if (idx === -1) return str;
    return str.substring(0, idx) + str.substring(idx + sub.length);
}

// ============================================================
// Input/Output (Browser-compatible)
// ============================================================

// Override console.log to also display in HTML
const _leash_original_log = console.log;
const _leash_output_buffer = [];

function _leash_browser_log(...args) {
    const message = args.map(a => String(a)).join(' ');
    _leash_output_buffer.push(message);
    _leash_original_log.apply(console, args);
    
    // Update HTML output if element exists
    const outputEl = document.getElementById('leash-output');
    if (outputEl) {
        outputEl.textContent += message + '\n';
        outputEl.scrollTop = outputEl.scrollHeight;
    }
}

console.log = _leash_browser_log;

// input/get() using browser prompt
function get(question) {
    return new Promise((resolve) => {
        const answer = prompt(question || '');
        resolve(answer !== null ? answer : '');
    });
}

// ============================================================
// File class simulation (Browser stub - limited functionality)
// ============================================================
const _leash_browser_files = {};

class LeashFile {
    constructor(fd, filePath, mode) {
        this.fd = fd;
        this.path = filePath;
        this.mode = mode;
        this._content = '';
        this._pos = 0;
        this._closed = false;
        
        if (mode.includes('r') && _leash_browser_files[filePath]) {
            this._content = _leash_browser_files[filePath];
        }
        if (mode === 'a' && _leash_browser_files[filePath]) {
            this._content = _leash_browser_files[filePath];
            this._pos = this._content.length;
        }
    }

    read() {
        if (this._closed) return null;
        if (this.mode.includes('w') || this.mode.includes('a')) {
            this._flush();
            if (_leash_browser_files[this.path]) {
                return _leash_browser_files[this.path];
            }
            return '';
        }
        const result = this._content.substring(this._pos);
        this._pos = this._content.length;
        return result;
    }

    write(text) {
        if (this._closed) return -1;
        if (this.mode.includes('r') && !this.mode.includes('+')) {
            return -1;
        }
        
        if (this.mode === 'w' || this.mode === 'wb') {
            if (this._pos + text.length > this._content.length) {
                this._content = this._content.substring(0, this._pos) + text;
            } else {
                this._content = this._content.substring(0, this._pos) + text + this._content.substring(this._pos + text.length);
            }
            this._pos += text.length;
        } else if (this.mode === 'a') {
            this._content += text;
            this._pos = this._content.length;
        } else if (this.mode.includes('+')) {
            if (this._pos + text.length > this._content.length) {
                this._content = this._content.substring(0, this._pos) + text;
            } else {
                this._content = this._content.substring(0, this._pos) + text + this._content.substring(this._pos + text.length);
            }
            this._pos += text.length;
        } else {
            this._content += text;
            this._pos = this._content.length;
        }
        
        if (this.mode.includes('w') || this.mode.includes('a') || this.mode.includes('+')) {
            this._flush();
        }
        
        return 0;
    }

    writeb(bytes) {
        if (this._closed) return -1;
        if (typeof bytes === 'string') {
            return this.write(bytes);
        }
        if (typeof bytes === 'number') {
            bytes = _leash_load(bytes);
        }
        if (Array.isArray(bytes)) {
            const text = String.fromCharCode(...bytes);
            return this.write(text);
        }
        return this.write(String(bytes));
    }

    readln() {
        if (this._closed) return null;
        
        if (this.mode.includes('w') || this.mode.includes('a')) {
            this._flush();
            if (_leash_browser_files[this.path]) {
                this._content = _leash_browser_files[this.path];
            }
        }
        
        if (this._pos >= this._content.length) {
            return null;
        }
        
        const newlineIdx = this._content.indexOf('\n', this._pos);
        let line;
        if (newlineIdx === -1) {
            line = this._content.substring(this._pos);
            this._pos = this._content.length;
        } else {
            line = this._content.substring(this._pos, newlineIdx);
            this._pos = newlineIdx + 1;
        }
        return line;
    }

    readb() {
        if (this._closed) return [];
        if (this.mode.includes('w') || this.mode.includes('a')) {
            this._flush();
            if (_leash_browser_files[this.path]) {
                const content = _leash_browser_files[this.path];
                const arr = [];
                for (let i = 0; i < content.length; i++) {
                    arr.push(content.charCodeAt(i));
                }
                return arr;
            }
            return [];
        }
        const result = this._content.substring(this._pos);
        this._pos = this._content.length;
        const arr = [];
        for (let i = 0; i < result.length; i++) {
            arr.push(result.charCodeAt(i));
        }
        return arr;
    }

    readlnb() {
        const line = this.readln();
        if (line === null) return [];
        const arr = [];
        for (let i = 0; i < line.length; i++) {
            arr.push(line.charCodeAt(i));
        }
        return arr;
    }

    replace(oldStr, newStr) {
        if (this._closed) return 0;
        this._flush();
        if (_leash_browser_files[this.path]) {
            let content = _leash_browser_files[this.path];
            const idx = content.indexOf(oldStr);
            if (idx === -1) return 0;
            content = content.substring(0, idx) + newStr + content.substring(idx + oldStr.length);
            _leash_browser_files[this.path] = content;
            this._content = content;
            return 1;
        }
        return 0;
    }

    replaceall(oldStr, newStr) {
        if (this._closed) return 0;
        this._flush();
        if (_leash_browser_files[this.path]) {
            let content = _leash_browser_files[this.path];
            const regex = new RegExp(oldStr.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g');
            const matches = content.match(regex);
            const count = matches ? matches.length : 0;
            content = content.replace(regex, newStr);
            _leash_browser_files[this.path] = content;
            this._content = content;
            return count;
        }
        return 0;
    }

    rewind() {
        if (this._closed) return;
        this._flush();
        if (_leash_browser_files[this.path]) {
            this._content = _leash_browser_files[this.path];
        }
        this._pos = 0;
    }

    close() {
        if (this._closed) return 0;
        this._flush();
        this._closed = true;
        return 0;
    }

    _flush() {
        if (this._closed) return;
        if (this.mode.includes('w') || this.mode.includes('a') || this.mode.includes('+')) {
            try {
                _leash_browser_files[this.path] = this._content;
            } catch (e) {}
        }
    }
}

const File = {
    open: function(filePath, mode) {
        try {
            const fd = Math.floor(Math.random() * 10000) + 1;
            const file = new LeashFile(fd, filePath, mode);
            return file;
        } catch (e) {
            return null;
        }
    },

    rename: function(oldPath, newPath) {
        try {
            if (_leash_browser_files[oldPath]) {
                _leash_browser_files[newPath] = _leash_browser_files[oldPath];
                delete _leash_browser_files[oldPath];
                return 0;
            }
            return -1;
        } catch (e) {
            return -1;
        }
    },

    delete: function(filePath) {
        try {
            if (_leash_browser_files[filePath]) {
                delete _leash_browser_files[filePath];
                return 0;
            }
            return -1;
        } catch (e) {
            return -1;
        }
    }
};

// ============================================================
// exec() - Not available in browser
// ============================================================
function exec(command, mode) {
    throw new Error("exec() is not available in browser environment");
}

// ============================================================
// choose()
// ============================================================
function choose(...args) {
    if (args.length === 0) return '';
    _leash_seed = (_leash_seed * 1103515245 + 12345) & 0x7FFFFFFF;
    const idx = _leash_seed % args.length;
    return args[idx];
}

// ============================================================
// sizeof()
// ============================================================
function sizeof(typeName) {
    const sizes = {
        'int': 4, 'uint': 4, 'float': 8, 'double': 8,
        'char': 1, 'bool': 1, 'string': 8, 'ptr': 8,
    };
    return sizes[typeName] || 8;
}

// ============================================================
// Type conversion helpers
// ============================================================
function toint(val) {
    return parseInt(val) || 0;
}

function tofloat(val) {
    return parseFloat(val) || 0.0;
}

function tostring(val) {
    if (val === null || val === undefined) return '';
    if (typeof val === 'object') {
        if (val._tag !== undefined) return _leash_display_union(val);
        return JSON.stringify(val);
    }
    if (typeof val === 'number' && !Number.isInteger(val)) {
        return val.toFixed(6);
    }
    return String(val);
}

// ============================================================
// Vector helper functions
// ============================================================
function vec_pushb(vec, item) { _leash_load(vec).push(item); }
function vec_popb(vec) { return _leash_load(vec).pop(); }
function vec_pushf(vec, item) { _leash_load(vec).unshift(item); }
function vec_popf(vec) { return _leash_load(vec).shift(); }
function vec_get(vec, idx) { return _leash_load(vec)[idx]; }
function vec_set(vec, idx, item) { _leash_load(vec)[idx] = item; }
function vec_size(vec) { return _leash_load(vec).length; }
function vec_clear(vec) { _leash_load(vec).length = 0; }
function vec_insert(vec, idx, item) { _leash_load(vec).splice(idx, 0, item); }
function vec_remove(vec, idx) { _leash_load(vec).splice(idx, 1); }
function vec_contains(vec, item) { return _leash_load(vec).includes(item); }

// ============================================================
// Array helper functions
// ============================================================
function array_size(arr) { return _leash_load(arr).length; }
function array_push(arr, item) { _leash_load(arr).push(item); }
function array_pop(arr) { return _leash_load(arr).pop(); }

// ============================================================
// String helper methods
// ============================================================
if (!String.prototype.toupper) {
    String.prototype.toupper = function() { return this.toUpperCase(); };
}
if (!String.prototype.tolower) {
    String.prototype.tolower = function() { return this.toLowerCase(); };
}
if (!String.prototype.trim) {
    String.prototype.trim = function() { return this.trim(); };
}
if (!String.prototype.split) {
    String.prototype.split = function(sep) { return this.split(sep); };
}
if (!String.prototype.replaceall) {
    String.prototype.replaceall = function(search, replacement) {
        return this.split(search).join(replacement);
    };
}
if (!String.prototype.contains) {
    String.prototype.contains = function(search) { return this.includes(search); };
}
if (!String.prototype.startswith) {
    String.prototype.startswith = function(search) { return this.startsWith(search); };
}
if (!String.prototype.endswith) {
    String.prototype.endswith = function(search) { return this.endsWith(search); };
}
if (!String.prototype.find) {
    String.prototype.find = function(search) { return this.indexOf(search); };
}
if (!String.prototype.substr) {
    String.prototype.substr = function(start, length) {
        if (length !== undefined) {
            return this.substring(start, start + length);
        }
        return this.substring(start);
    };
}

// ============================================================
// Process exit
// ============================================================
function _leash_exit(code) {
    console.log("Program exited with code: " + (code || 0));
}

// ============================================================
// Yield helper for loops (simplified for browser)
// ============================================================
let _leash_yield_counter = 0;
function _leash_maybe_yield() {
    _leash_yield_counter++;
    if (_leash_yield_counter >= 1000) {
        _leash_yield_counter = 0;
        // In browser, we can't really yield to signal handling
        // Just use setTimeout(0) to prevent UI freezing
        // This is a no-op in synchronous context but helps in async
    }
}
