const $ = (id) => document.getElementById(id);
let connection = null;

function currentConfig() {
    return {
        base_url: $('base-url').value.trim() || 'https://api.openai.com/v1',
        model: $('model').value.trim() || 'gpt-4o-mini',
        api_key: $('api-key').value.trim(),
        bar_count: Math.max(20, Math.min(80, Number.parseInt($('bar-count').value, 10) || 80)),
        include_indicator: $('include-indicator').checked,
        indicator_type: $('indicator-type').value,
        include_chip: $('include-chip').checked,
        strategy: $('strategy').value.trim(),
    };
}

function writeLog(message, level = 'info') {
    const log = $('log');
    if (log.querySelector('.muted')) log.replaceChildren();
    const item = document.createElement('p');
    item.className = level;
    item.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
    log.append(item);
    log.scrollTop = log.scrollHeight;
}

async function requestJson(path, options = {}) {
    const response = await fetch(path, {
        headers: { 'Content-Type': 'application/json' },
        ...options,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || payload.message || `HTTP ${response.status}`);
    return payload;
}

function applyConfig(config) {
    $('base-url').value = config.base_url || 'https://api.openai.com/v1';
    $('model').value = config.model || 'gpt-4o-mini';
    $('api-key').value = config.api_key || '';
    $('bar-count').value = config.bar_count || 80;
    $('include-indicator').checked = config.include_indicator !== false;
    $('indicator-type').value = config.indicator_type || 'MACD';
    $('include-chip').checked = config.include_chip !== false;
    $('strategy').value = config.strategy || '';
}

async function saveConfig(quiet = false) {
    const config = currentConfig();
    await requestJson('/api/config', { method: 'POST', body: JSON.stringify(config) });
    if (!quiet) writeLog('设置已保存在本机 data 文件夹。', 'success');
    return config;
}

async function refreshConnection() {
    const state = $('connection-state');
    try {
        const payload = await requestJson('/api/connect', { method: 'POST', body: '{}' });
        connection = payload.status === 'connected' ? payload : null;
        state.textContent = connection
            ? `已连接：${payload.current_user || '用户'} · ${payload.training_id || '无训练会话'}`
            : (payload.message || '未连接主程序');
        state.className = `badge ${connection ? 'ok' : 'error'}`;
        if (connection) writeLog(`主程序连接成功，当前训练 ${payload.training_id || '未开启'}。`, 'success');
        else writeLog(payload.message || '主程序尚未开启 AI 接入或训练。', 'error');
        return connection;
    } catch (error) {
        connection = null;
        state.textContent = '连接检查失败';
        state.className = 'badge error';
        writeLog(error.message, 'error');
        return null;
    }
}

function requireConnection() {
    if (!connection?.base_url || !connection?.training_id) {
        throw new Error('请先在主训练器开启训练和 AI/API 接入，再检查连接。');
    }
    return connection;
}

async function analyze() {
    const button = $('analyze-btn');
    button.disabled = true;
    try {
        requireConnection();
        const config = await saveConfig(true);
        const result = await requestJson('/api/action/analyze', { method: 'POST', body: JSON.stringify(config) });
        for (const entry of result.logs || []) writeLog(entry.message, entry.level || 'info');
        if (result.trade) writeLog(`${result.action} ${result.trade.quantity} 股，成交价 $${Number(result.trade.price).toFixed(2)}。`, 'success');
        if (result.finished) writeLog('训练已结束。', 'success');
    } catch (error) {
        writeLog(error.message, 'error');
    } finally {
        button.disabled = false;
    }
}

async function initialize() {
    try {
        applyConfig(await requestJson('/api/config'));
    } catch (error) {
        writeLog(`读取设置失败：${error.message}`, 'error');
    }
    await refreshConnection();
}

$('save-btn').addEventListener('click', async () => {
    try { await saveConfig(); } catch (error) { writeLog(error.message, 'error'); }
});
$('connect-btn').addEventListener('click', refreshConnection);
$('analyze-btn').addEventListener('click', analyze);
$('clear-btn').addEventListener('click', () => $('log').replaceChildren());
initialize();
