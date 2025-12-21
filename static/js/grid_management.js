// 网格策略管理页面 JavaScript
// 接入 FastAPI 后端接口

// 全局状态
let currentStrategyStatus = null;
let statusUpdateInterval = null;

// 统一禁用原生 confirm 和 alert，防止旧代码或其他脚本弹出系统确认框
// 所有确认操作改为使用自定义模态框 showConfirmModal
// 所有提示操作改为使用自定义 Toast 组件
window.confirm = function (message) {
    console.warn('屏蔽原生 confirm 调用:', message);
    return true;
};

window.alert = function (message) {
    console.warn('屏蔽原生 alert 调用:', message);
    showToast(message, 'info');
};

// DOM 元素（将在DOMContentLoaded中初始化）
let gridConfigForm = null;
let gridConfigModal = null;
let closeConfigModal = null;
let cancelConfigBtn = null;
// 通用确认弹窗
let confirmModal = null;
let closeConfirmModal = null;
let confirmTitleEl = null;
let confirmMessageEl = null;
let confirmOkBtn = null;
let confirmCancelBtn = null;
let pendingConfirmAction = null; // 当前等待确认的操作（函数）
let startStrategyBtn = null;
let stopStrategyBtn = null;
let marketTypeSelect = null;
let coTypeSelect = null;
let coTypeField = null;
let symbolInput = null;
let symbolDatalist = null; // 已废弃，保留变量名避免报错
let symbolDropdown = null;
let leverageInput = null;
let leverageSlider = null;
let directionField = null;
let leverageField = null;
let strategyStatusCard = null;
let strategyStatusBadge = null;
let statusSymbol = null;
let statusElapsedTime = null;
let statusPriceRange = null;
let statusCurrentPrice = null;
let statusStartPrice = null;
let statusArbitrageCount = null;
let statusMarketState = null;
let refreshPriceBtn = null;
let connectionBadgeText = null; // 顶部连接状态文本

// 收益相关元素
let statusGridProfit = null;
let statusGridProfitRate = null;
let statusUnrealizedProfit = null;
let statusUnrealizedProfitRate = null;
// 交易对额外展示元素（多空颜色 & 杠杆）
let statusDirectionEl = null;
let statusLeverageEl = null;

// 统计数据元素
let totalStrategiesEl = null;
let runningStrategiesEl = null;
let stoppedStrategiesEl = null;
let totalInvestmentEl = null;
let totalPnLEl = null;
let totalVolumeEl = null;
let avgReturnEl = null;

// 交易对缓存
let contractSymbolsCache = null; // [{ symbol, name, ... }]
let spotSymbolsCache = null;     // [{ symbol, name }]

// 从后端获取指定市场类型的交易对列表
async function fetchSymbolsByMarketType(marketType, coType) {
    if (!marketType) {
        marketType = 'contract';
    }

    // 使用缓存避免重复请求（合约需要根据 coType 区分缓存）
    if (marketType === 'contract') {
        // 合约：根据 coType 区分缓存，默认使用 '1'
        const effectiveCoType = coType || '1';
        if (window[`contractSymbolsCache_${effectiveCoType}`]) {
            return window[`contractSymbolsCache_${effectiveCoType}`];
        }
    } else if (marketType === 'spot' && spotSymbolsCache) {
        return spotSymbolsCache;
    }

    // 构建 API URL
    let apiUrl = `/api/symbols?market_type=${encodeURIComponent(marketType)}`;
    // 合约市场：如果指定了 coType 则传递，否则后端会使用默认值 1
    if (marketType === 'contract' && coType) {
        apiUrl += `&co_type=${encodeURIComponent(coType)}`;
    }

    const res = await fetch(apiUrl);
    if (!res.ok) {
        throw new Error(`获取交易对列表失败: ${res.status}`);
    }

    const data = await res.json();
    if (data.status !== 'success' || !data.data || !Array.isArray(data.data.symbols)) {
        throw new Error('交易对列表返回格式不正确');
    }

    let list;
    if (marketType === 'contract') {
        // 合约：保留 leverTypes 信息，用于限制杠杆倍数
        list = data.data.symbols.map((item) => ({
            symbol: item.symbol,
            name: item.name || '',
            leverTypes: item.leverTypes || '',
        }));
        // 根据 coType 缓存（使用默认值 '1' 如果 coType 为空）
        const effectiveCoType = coType || '1';
        window[`contractSymbolsCache_${effectiveCoType}`] = list;
    } else {
        // 现货：只需要 symbol 和 name
        list = data.data.symbols.map((item) => ({
            symbol: item.symbol,
            name: item.name || '',
        }));
        spotSymbolsCache = list;
    }

    return list;
}

// 填充自定义下拉列表
function populateSymbolDatalist(symbols) {
    if (!symbolDropdown) return;

    symbolDropdown.innerHTML = '';
    symbols.forEach((item) => {
        if (!item.symbol) return;
        const option = document.createElement('div');
        option.className = 'symbol-option';
        option.textContent = item.symbol + (item.name ? ` - ${item.name}` : '');
        option.dataset.symbol = item.symbol;
        option.addEventListener('mousedown', (e) => {
            e.preventDefault();
            if (symbolInput) {
                symbolInput.value = item.symbol;
                symbolInput.dispatchEvent(new Event('change'));
            }
            hideSymbolDropdown();
        });
        symbolDropdown.appendChild(option);
    });

    if (symbols.length > 0) {
        showSymbolDropdown();
    } else {
        hideSymbolDropdown();
    }
}

// 根据当前市场类型加载交易对列表
async function loadSymbolsForCurrentMarket() {
    try {
        const marketType = marketTypeSelect ? marketTypeSelect.value : 'contract';
        const coType = (marketType === 'contract' && coTypeSelect) ? coTypeSelect.value : null;
        const allSymbols = await fetchSymbolsByMarketType(marketType || 'contract', coType);

        // 根据当前输入进行本地过滤（按 symbol 或 name 模糊匹配）
        let keyword = '';
        if (symbolInput) {
            keyword = symbolInput.value.trim().toUpperCase();
        }

        const symbols = keyword
            ? allSymbols.filter((item) => {
                  const sym = (item.symbol || '').toUpperCase();
                  const name = (item.name || '').toUpperCase();
                  return sym.includes(keyword) || name.includes(keyword);
              })
            : allSymbols;

        populateSymbolDatalist(symbols);
        // 加载完交易对后，更新杠杆输入状态
        await updateLeverageBySymbol();
    } catch (error) {
        console.error('加载交易对列表失败:', error);
    }
}

// 对于现货/合约，设置杠杆输入框的状态与范围
function setLeverageForSpot() {
    if (!leverageInput) return;
    leverageInput.disabled = false; // 保持可提交
    leverageInput.readOnly = true;  // 只读，防止用户修改
    leverageInput.min = '1';
    leverageInput.max = '1';
    leverageInput.value = '1';
    leverageInput.classList.add('field-input-readonly');
    if (leverageSlider) {
        leverageSlider.min = '1';
        leverageSlider.max = '1';
        leverageSlider.value = '1';
        leverageSlider.disabled = true;
    }
    if (directionField) directionField.style.display = 'none';
    if (leverageField) leverageField.style.display = 'none';
}

function setLeverageRange(min, max) {
    if (!leverageInput) return;
    const minVal = Math.max(1, parseInt(min || 1, 10));
    const maxVal = Math.max(minVal, parseInt(max || minVal, 10));

    // 数字输入
    leverageInput.disabled = false;
    leverageInput.readOnly = false;
    leverageInput.min = String(minVal);
    leverageInput.max = String(maxVal);
    let cur = parseInt(leverageInput.value || '0', 10);
    if (Number.isNaN(cur) || cur < minVal) cur = minVal;
    if (cur > maxVal) cur = maxVal;
    leverageInput.value = String(cur);
    leverageInput.classList.remove('field-input-readonly');

    // 滑杆
    if (leverageSlider) {
        leverageSlider.disabled = false;
        leverageSlider.min = String(minVal);
        leverageSlider.max = String(maxVal);
        leverageSlider.value = String(cur);
    }
    if (directionField) directionField.style.display = '';
    if (leverageField) leverageField.style.display = '';
}

// 根据当前市场类型和所选交易对，更新杠杆倍数范围/禁用状态
async function updateLeverageBySymbol() {
    if (!marketTypeSelect || !leverageInput) return;
    const marketType = marketTypeSelect.value || 'contract';

    // 现货：无杠杆，固定为 1 倍，并禁用输入
    if (marketType === 'spot') {
        setLeverageForSpot();
        return;
    }

    // 合约：根据 leverTypes 限制杠杆范围
    const symbol = symbolInput ? symbolInput.value.trim().toUpperCase() : '';
    if (!symbol) {
        // 未选择交易对时，使用默认范围 1-100
        setLeverageRange(1, 100);
        return;
    }

    try {
        // 获取当前选择的 coType，确保从正确的缓存中获取交易对列表
        const coType = (marketType === 'contract' && coTypeSelect) ? coTypeSelect.value : null;
        const symbols = await fetchSymbolsByMarketType(marketType, coType);
        const meta = symbols.find((item) => (item.symbol || '').toUpperCase() === symbol);
        if (!meta || !meta.leverTypes) {
            setLeverageRange(1, 100);
            return;
        }

        const parts = String(meta.leverTypes)
            .split(',')
            .map((p) => parseInt(p.trim(), 10))
            .filter((n) => !Number.isNaN(n) && n > 0);

        if (!parts.length) {
            setLeverageRange(1, 100);
            return;
        }

        const minLev = Math.min(...parts);
        const maxLev = Math.max(...parts);
        // 先设置范围
        setLeverageRange(minLev, maxLev);
        // 切换/选择 symbol 后默认使用最大杠杆倍数，使滑杆在最右侧
        leverageInput.value = String(maxLev);
        if (leverageSlider) {
            leverageSlider.value = String(maxLev);
        }
    } catch (error) {
        console.error('根据交易对更新杠杆倍数失败:', error);
        setLeverageRange(1, 100);
    }
}

// 显示/隐藏自定义下拉
function showSymbolDropdown() {
    if (!symbolDropdown || !symbolInput) return;
    const rect = symbolInput.getBoundingClientRect();
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
    const available = Math.max(120, viewportHeight - rect.bottom - 16);
    symbolDropdown.style.maxHeight = `${available}px`;
    symbolDropdown.style.display = 'block';
}

function hideSymbolDropdown() {
    if (!symbolDropdown) return;
    symbolDropdown.style.display = 'none';
}

// 初始化DOM元素
function initDOMElements() {
    gridConfigForm = document.getElementById('gridConfigForm');
    gridConfigModal = document.getElementById('gridConfigModal');
    closeConfigModal = document.getElementById('closeConfigModal');
    cancelConfigBtn = document.getElementById('cancelConfigBtn');
    // 确认弹窗相关
    confirmModal = document.getElementById('confirmModal');
    closeConfirmModal = document.getElementById('closeConfirmModal');
    confirmTitleEl = document.getElementById('confirmTitle');
    confirmMessageEl = document.getElementById('confirmMessage');
    confirmOkBtn = document.getElementById('confirmOkBtn');
    confirmCancelBtn = document.getElementById('confirmCancelBtn');
    startStrategyBtn = document.getElementById('startStrategyBtn');
    stopStrategyBtn = document.getElementById('stopStrategyBtn');
    marketTypeSelect = document.getElementById('marketType');
    coTypeSelect = document.getElementById('coType');
    coTypeField = document.getElementById('coTypeField');
    symbolInput = document.getElementById('symbol');
    symbolDropdown = document.getElementById('symbolDropdown');
    leverageInput = document.getElementById('leverage');
    leverageSlider = document.getElementById('leverageSlider');
    directionField = document.getElementById('directionField');
    leverageField = document.getElementById('leverageField');
    strategyStatusCard = document.getElementById('strategyStatusCard');
    strategyStatusBadge = document.getElementById('strategyStatusBadge');
    statusSymbol = document.getElementById('statusSymbol');
    statusElapsedTime = document.getElementById('statusElapsedTime');
    statusPriceRange = document.getElementById('statusPriceRange');
    statusCurrentPrice = document.getElementById('statusCurrentPrice');
    statusStartPrice = document.getElementById('statusStartPrice');
    statusArbitrageCount = document.getElementById('statusArbitrageCount');
    statusMarketState = document.getElementById('statusMarketState');
    refreshPriceBtn = document.getElementById('refreshPriceBtn');
    statusGridProfit = document.getElementById('statusGridProfit');
    statusGridProfitRate = document.getElementById('statusGridProfitRate');
    statusUnrealizedProfit = document.getElementById('statusUnrealizedProfit');
    statusUnrealizedProfitRate = document.getElementById('statusUnrealizedProfitRate');
    statusDirectionEl = document.getElementById('statusDirection');
    statusLeverageEl = document.getElementById('statusLeverage');
    
    totalStrategiesEl = document.getElementById('totalStrategies');
    runningStrategiesEl = document.getElementById('runningStrategies');
    stoppedStrategiesEl = document.getElementById('stoppedStrategies');
    totalInvestmentEl = document.getElementById('totalInvestment');
    totalPnLEl = document.getElementById('totalPnL');
    totalVolumeEl = document.getElementById('totalVolume');
    avgReturnEl = document.getElementById('avgReturn');
    
    // 顶部连接状态
    const connectionBadge = document.querySelector('.connection-badge .badge-text');
    if (connectionBadge) {
        connectionBadgeText = connectionBadge;
    }
}

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    console.log('DOM内容已加载，开始初始化...');
    initDOMElements();
    
    // 初始化时根据市场类型显示/隐藏 coType 字段
    if (marketTypeSelect && coTypeField) {
        if (marketTypeSelect.value === 'contract') {
            coTypeField.style.display = '';
        } else {
            coTypeField.style.display = 'none';
        }
    }
    
    initEventListeners();
    loadStrategyStatus();
    // 每5秒更新一次状态
    statusUpdateInterval = setInterval(loadStrategyStatus, 5000);
    console.log('初始化完成');
});

// 关闭配置模态框
function closeConfigModalHandler() {
    if (gridConfigModal) {
        gridConfigModal.classList.remove('active');
    }
    if (gridConfigForm) {
        gridConfigForm.reset();
    }
}

// 打开通用确认弹窗
function showConfirmModal({ title = '请确认操作', message = '确定要执行此操作吗？', onConfirm } = {}) {
    if (!confirmModal) return;
    if (confirmTitleEl) {
        confirmTitleEl.textContent = title;
    }
    if (confirmMessageEl) {
        confirmMessageEl.textContent = message;
    }
    pendingConfirmAction = typeof onConfirm === 'function' ? onConfirm : null;
    confirmModal.classList.add('active');
}

// 关闭通用确认弹窗
function closeConfirmModalHandler() {
    if (!confirmModal) return;
    confirmModal.classList.remove('active');
    pendingConfirmAction = null;
}

// ==================== Toast 提示组件 ====================

/**
 * 显示 Toast 提示
 * @param {string} message - 提示消息
 * @param {string} type - 提示类型：'success', 'error', 'warning', 'info'
 * @param {number} duration - 显示时长（毫秒），默认 3000，0 表示不自动关闭
 */
function showToast(message, type = 'info', duration = 3000) {
    const container = document.getElementById('toastContainer');
    if (!container) {
        console.error('Toast 容器未找到');
        return;
    }

    // 创建 Toast 元素
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;

    // 图标 SVG
    const iconSvg = getToastIcon(type);
    const icon = document.createElement('div');
    icon.className = 'toast-icon';
    icon.innerHTML = iconSvg;

    // 内容
    const content = document.createElement('div');
    content.className = 'toast-content';
    content.textContent = message;

    // 关闭按钮
    const closeBtn = document.createElement('button');
    closeBtn.className = 'toast-close';
    closeBtn.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"/>
            <line x1="6" y1="6" x2="18" y2="18"/>
        </svg>
    `;
    closeBtn.addEventListener('click', () => {
        removeToast(toast);
    });

    toast.appendChild(icon);
    toast.appendChild(content);
    toast.appendChild(closeBtn);
    container.appendChild(toast);

    // 自动关闭
    if (duration > 0) {
        setTimeout(() => {
            removeToast(toast);
        }, duration);
    }

    return toast;
}

/**
 * 获取 Toast 图标 SVG
 */
function getToastIcon(type) {
    const icons = {
        success: `
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="20 6 9 17 4 12"/>
            </svg>
        `,
        error: `
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"/>
                <line x1="12" y1="8" x2="12" y2="12"/>
                <line x1="12" y1="16" x2="12.01" y2="16"/>
            </svg>
        `,
        warning: `
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
                <line x1="12" y1="9" x2="12" y2="13"/>
                <line x1="12" y1="17" x2="12.01" y2="17"/>
            </svg>
        `,
        info: `
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"/>
                <line x1="12" y1="16" x2="12" y2="12"/>
                <line x1="12" y1="8" x2="12.01" y2="8"/>
            </svg>
        `
    };
    return icons[type] || icons.info;
}

/**
 * 移除 Toast
 */
function removeToast(toast) {
    if (!toast || !toast.parentNode) return;
    
    toast.classList.add('toast-exit');
    setTimeout(() => {
        if (toast.parentNode) {
            toast.parentNode.removeChild(toast);
        }
    }, 300);
}

// 初始化事件监听器
function initEventListeners() {
    try {
        console.log('初始化事件监听器...');
        console.log('startStrategyBtn:', startStrategyBtn);
        console.log('stopStrategyBtn:', stopStrategyBtn);
        console.log('gridConfigModal:', gridConfigModal);
        
        // 启动策略按钮 - 打开配置模态框
        if (startStrategyBtn && typeof startStrategyBtn.addEventListener === 'function') {
            startStrategyBtn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                console.log('启动策略按钮被点击');
                if (gridConfigModal) {
                    gridConfigModal.classList.add('active');
                    console.log('模态框已打开');
                    // 打开模态框时，根据当前市场类型加载交易对列表
                    loadSymbolsForCurrentMarket().catch((err) => {
                        console.error('加载交易对列表失败:', err);
                    });
                } else {
                    console.error('gridConfigModal 元素未找到');
                }
            });
            console.log('启动策略按钮事件监听器已绑定');
        } else {
            console.error('startStrategyBtn 元素未找到或不是有效的DOM元素');
        }

        // 关闭配置模态框
        if (closeConfigModal && typeof closeConfigModal.addEventListener === 'function') {
            closeConfigModal.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                closeConfigModalHandler();
            });
            console.log('关闭按钮事件监听器已绑定');
        } else {
            console.error('closeConfigModal 元素未找到或不是有效的DOM元素');
        }
        
        if (cancelConfigBtn && typeof cancelConfigBtn.addEventListener === 'function') {
            cancelConfigBtn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                closeConfigModalHandler();
            });
            console.log('取消按钮事件监听器已绑定');
        } else {
            console.error('cancelConfigBtn 元素未找到或不是有效的DOM元素');
        }

        // 确认弹窗按钮
        if (closeConfirmModal && typeof closeConfirmModal.addEventListener === 'function') {
            closeConfirmModal.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                closeConfirmModalHandler();
            });
        }
        if (confirmCancelBtn && typeof confirmCancelBtn.addEventListener === 'function') {
            confirmCancelBtn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                closeConfirmModalHandler();
            });
        }
        if (confirmOkBtn && typeof confirmOkBtn.addEventListener === 'function') {
            confirmOkBtn.addEventListener('click', async (e) => {
                e.preventDefault();
                e.stopPropagation();
                const action = pendingConfirmAction;
                // 先关闭弹窗，再执行动作，避免动作过程中的 UI 抖动影响弹窗
                closeConfirmModalHandler();
                if (typeof action === 'function') {
                    try {
                        await action();
                    } catch (err) {
                        console.error('确认操作执行失败:', err);
                    }
                }
            });
        }

        // 点击模态框外部关闭
        if (gridConfigModal && typeof gridConfigModal.addEventListener === 'function') {
            gridConfigModal.addEventListener('click', (e) => {
                if (e.target === gridConfigModal) {
                    closeConfigModalHandler();
                }
            });
        }

        // 市场类型切换时，重新加载对应市场的交易对列表
        if (marketTypeSelect && typeof marketTypeSelect.addEventListener === 'function') {
            marketTypeSelect.addEventListener('change', () => {
                // 根据市场类型显示/隐藏 coType 字段
                if (coTypeField) {
                    if (marketTypeSelect.value === 'contract') {
                        coTypeField.style.display = '';
                    } else {
                        coTypeField.style.display = 'none';
                    }
                }
                loadSymbolsForCurrentMarket().catch((err) => {
                    console.error('加载交易对列表失败:', err);
                });
                // 市场类型切换时，更新杠杆输入状态
                updateLeverageBySymbol().catch((err) => {
                    console.error('更新杠杆倍数限制失败:', err);
                });
            });
        }

        // coType 切换时，重新加载交易对列表
        if (coTypeSelect && typeof coTypeSelect.addEventListener === 'function') {
            coTypeSelect.addEventListener('change', () => {
                if (marketTypeSelect && marketTypeSelect.value === 'contract') {
                    loadSymbolsForCurrentMarket().catch((err) => {
                        console.error('加载交易对列表失败:', err);
                    });
                }
            });
        }

        // 交易对输入变化时，根据所选交易对更新杠杆范围
        if (symbolInput && typeof symbolInput.addEventListener === 'function') {
            const handler = () => {
                updateLeverageBySymbol().catch((err) => {
                    console.error('更新杠杆倍数限制失败:', err);
                });
            };
            symbolInput.addEventListener('input', () => {
                // 根据输入过滤下拉列表
                loadSymbolsForCurrentMarket().catch((err) => {
                    console.error('加载交易对列表失败:', err);
                });
            });
            symbolInput.addEventListener('change', handler);
            symbolInput.addEventListener('blur', handler);

            // 失去焦点后稍后隐藏下拉（保留点击选中）
            symbolInput.addEventListener('blur', () => {
                setTimeout(hideSymbolDropdown, 150);
            });
            symbolInput.addEventListener('focus', () => {
                loadSymbolsForCurrentMarket().catch((err) => {
                    console.error('加载交易对列表失败:', err);
                });
            });
        }

        // 杠杆滑杆与数字输入联动
        if (leverageSlider && leverageInput) {
            leverageSlider.addEventListener('input', () => {
                leverageInput.value = leverageSlider.value;
            });
            leverageInput.addEventListener('input', () => {
                const val = leverageInput.value;
                if (val !== '' && !Number.isNaN(parseInt(val, 10))) {
                    leverageSlider.value = val;
                }
            });
        }

        // 停止策略按钮
        if (stopStrategyBtn && typeof stopStrategyBtn.addEventListener === 'function') {
            stopStrategyBtn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                console.log('停止策略按钮被点击');
                handleStopStrategy();
            });
            console.log('停止策略按钮事件监听器已绑定');
        } else {
            console.error('stopStrategyBtn 元素未找到或不是有效的DOM元素');
        }

        // 刷新价格按钮
        if (refreshPriceBtn && typeof refreshPriceBtn.addEventListener === 'function') {
            refreshPriceBtn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                console.log('刷新价格按钮被点击');
                loadStrategyStatus();
            });
            console.log('刷新价格按钮事件监听器已绑定');
        }

        // 表单提交
        if (gridConfigForm && typeof gridConfigForm.addEventListener === 'function') {
            gridConfigForm.addEventListener('submit', (e) => {
                e.preventDefault();
                console.log('表单提交');
                handleStartStrategy();
            });
            console.log('表单提交事件监听器已绑定');
        } else {
            console.error('gridConfigForm 元素未找到或不是有效的DOM元素');
        }
        
        console.log('事件监听器初始化完成');
    } catch (error) {
        console.error('初始化事件监听器时出错:', error);
        console.error('错误堆栈:', error.stack);
    }
}

// 加载策略状态
async function loadStrategyStatus() {
    try {
        const res = await fetch('/api/status');
        if (!res.ok) {
            // 如果策略未运行，不显示错误，但仍尝试更新连接状态
            if (res.status === 500) {
                updateUIForNoStrategy();
                // 即使没有策略，也尝试获取连接状态
                updateConnectionStatus(null);
                return;
            }
            throw new Error(`获取状态失败: ${res.status}`);
        }

        const data = await res.json();
        if (data.status === 'success' && data.data) {
            currentStrategyStatus = data.data;
            // 检查策略是否在运行
            const isRunning = data.data.running || data.data.status === 'running';
            if (isRunning) {
                updateUIForRunningStrategy(data.data);
            } else {
                // 策略已停止，隐藏状态卡片并重置概览
                updateUIForNoStrategy();
            }
            // 更新连接状态（使用 connected 字段）
            updateConnectionStatus(data.data.connected);
        } else {
            updateUIForNoStrategy();
            updateConnectionStatus(null);
        }
    } catch (error) {
        console.error('加载策略状态失败:', error);
        updateUIForNoStrategy();
        updateConnectionStatus(null);
    }
    // 无论成功或失败，请求已完成，此时如果按钮处于「加载中」但状态未变更，恢复到合适的可点击状态
    resetButtonsAfterStatusLoad();
}

// 更新顶部连接状态
function updateConnectionStatus(connected) {
    if (!connectionBadgeText) return;
    
    const badgeDot = document.querySelector('.connection-badge .badge-dot');
    
    if (connected === true) {
        connectionBadgeText.textContent = '已连接';
        if (badgeDot) {
            badgeDot.style.backgroundColor = '#10B981'; // 绿色表示已连接
        }
    } else {
        // connected === false 或 null 或 undefined，都显示"未连接"
        connectionBadgeText.textContent = '未连接';
        if (badgeDot) {
            badgeDot.style.backgroundColor = '#EF4444'; // 红色表示未连接
        }
    }
}

// 更新UI - 策略运行中
function updateUIForRunningStrategy(status) {
    // 更新状态卡片
    if (strategyStatusCard) {
        strategyStatusCard.style.display = 'block';
    }

    // 更新状态徽章
    if (strategyStatusBadge) {
        const isRunning = status.running || status.status === 'running';
        strategyStatusBadge.textContent = isRunning ? '运行中' : '已停止';
        strategyStatusBadge.className = `status-badge ${isRunning ? 'running' : 'stopped'}`;
    }

    const summary = status.summary || {};

    // 1. 交易对(多空，倍数) + 已运行时间
    if (statusSymbol) {
        if (status.symbol) {
            statusSymbol.textContent = status.symbol;
        } else {
            statusSymbol.textContent = '--';
        }
    }

    if (statusDirectionEl) {
        const directionText = status.direction === 'long' ? '做多' : '做空';
        statusDirectionEl.textContent = directionText;
        statusDirectionEl.className = 'status-direction';
        if (status.direction === 'long') {
            statusDirectionEl.classList.add('direction-long');
        } else if (status.direction === 'short') {
            statusDirectionEl.classList.add('direction-short');
        }
    }

    if (statusLeverageEl) {
        const leverage = status.leverage || 1;
        statusLeverageEl.textContent = `${leverage}X`;
    }

    // 2. 已运行时间
    if (statusElapsedTime) {
        if (status.elapsed_time) {
            statusElapsedTime.textContent = status.elapsed_time;
        } else {
            statusElapsedTime.textContent = '--';
        }
    }

    // 3. 价格区间
    if (statusPriceRange) {
        if (status.price_range && Array.isArray(status.price_range) && status.price_range.length === 2) {
            statusPriceRange.textContent = `${formatNumber(status.price_range[0])} - ${formatNumber(status.price_range[1])}`;
        } else {
            statusPriceRange.textContent = '--';
        }
    }

    // 4. 当前价格
    if (statusCurrentPrice) {
        if (status.current_price) {
            statusCurrentPrice.textContent = formatNumber(status.current_price);
        } else {
            statusCurrentPrice.textContent = '--';
        }
    }

    // 5. 启动价格
    if (statusStartPrice) {
        if (status.start_price) {
            statusStartPrice.textContent = formatNumber(status.start_price);
        } else {
            statusStartPrice.textContent = '--';
        }
    }

    // 6. 市场状态
    if (statusMarketState) {
        if (status.is_trading_hours !== undefined) {
            statusMarketState.textContent = status.is_trading_hours ? '开市' : '休市';
            statusMarketState.className = `status-info-value ${status.is_trading_hours ? 'positive' : 'negative'}`;
        } else {
            statusMarketState.textContent = '--';
            statusMarketState.className = 'status-info-value';
        }
    }

    // 7. 套利次数
    if (statusArbitrageCount) {
        if (summary.arbitrage_count !== undefined && summary.arbitrage_count !== null) {
            statusArbitrageCount.textContent = summary.arbitrage_count.toString();
        } else {
            statusArbitrageCount.textContent = '--';
        }
    }

    // 8. 网格收益（已实现）
    if (statusGridProfit) {
        const gridProfit = summary.grid_profit;
        if (gridProfit !== undefined && gridProfit !== null) {
            const cls = gridProfit >= 0 ? 'positive' : 'negative';
            const prefix = gridProfit >= 0 ? '+' : '-';
            statusGridProfit.textContent = `${prefix}$${formatNumber(Math.abs(gridProfit))}`;
            statusGridProfit.className = `status-info-value ${cls}`;
        } else {
            statusGridProfit.textContent = '--';
            statusGridProfit.className = 'status-info-value';
        }
    }
    if (statusGridProfitRate) {
        // 优先使用后端提供的 grid_return，若没有则用 total_investment 计算
        let gridReturn = summary.grid_return;
        if ((gridReturn === undefined || gridReturn === null) && summary.total_investment) {
            const gp = summary.grid_profit;
            if (gp !== undefined && gp !== null) {
                gridReturn = (gp / summary.total_investment) * 100;
            }
        }
        if (gridReturn !== undefined && gridReturn !== null) {
            const cls = gridReturn >= 0 ? 'positive' : 'negative';
            const prefix = gridReturn >= 0 ? '+' : '-';
            statusGridProfitRate.textContent = `${prefix}${formatNumber(Math.abs(gridReturn))}%`;
            statusGridProfitRate.className = `status-info-percent ${cls}`;
        } else {
            statusGridProfitRate.textContent = '--';
            statusGridProfitRate.className = 'status-info-percent';
        }
    }

    // 9. 未实现收益（浮动盈亏）
    if (statusUnrealizedProfit) {
        const upnl = summary.unrealized_pnl;
        if (upnl !== undefined && upnl !== null) {
            const cls = upnl >= 0 ? 'positive' : 'negative';
            const prefix = upnl >= 0 ? '+' : '-';
            statusUnrealizedProfit.textContent = `${prefix}$${formatNumber(Math.abs(upnl))}`;
            statusUnrealizedProfit.className = `status-info-value ${cls}`;
        } else {
            statusUnrealizedProfit.textContent = '--';
            statusUnrealizedProfit.className = 'status-info-value';
        }
    }
    if (statusUnrealizedProfitRate) {
        // 优先使用后端提供的 unrealized_return，若没有则用 total_investment 计算
        let unrealizedReturn = summary.unrealized_return;
        if ((unrealizedReturn === undefined || unrealizedReturn === null) && summary.total_investment) {
            const upnl = summary.unrealized_pnl;
            if (upnl !== undefined && upnl !== null) {
                unrealizedReturn = (upnl / summary.total_investment) * 100;
            }
        }
        if (unrealizedReturn !== undefined && unrealizedReturn !== null) {
            const cls = unrealizedReturn >= 0 ? 'positive' : 'negative';
            const prefix = unrealizedReturn >= 0 ? '+' : '-';
            statusUnrealizedProfitRate.textContent = `${prefix}${formatNumber(Math.abs(unrealizedReturn))}%`;
            statusUnrealizedProfitRate.className = `status-info-percent ${cls}`;
        } else {
            statusUnrealizedProfitRate.textContent = '--';
            statusUnrealizedProfitRate.className = 'status-info-percent';
        }
    }

    // 更新按钮状态
    if (startStrategyBtn) {
        const isRunning = status.running || status.status === 'running';
        startStrategyBtn.disabled = isRunning;
        const span = startStrategyBtn.querySelector('span');
        if (span) {
            span.textContent = isRunning ? '启动策略' : '启动策略';
        }
    }
    if (stopStrategyBtn) {
        const isRunning = status.running || status.status === 'running';
        stopStrategyBtn.disabled = !isRunning;
        const span = stopStrategyBtn.querySelector('span');
        if (span) {
            span.textContent = '停止策略';
        }
    }

    // 更新统计信息
    updateStatisticsFromStatus(status);
}

// 重置策略状态卡片
function resetStrategyStatusCard() {
    if (statusSymbol) statusSymbol.textContent = '--';
    if (statusElapsedTime) statusElapsedTime.textContent = '--';
    if (statusPriceRange) statusPriceRange.textContent = '--';
    if (statusCurrentPrice) statusCurrentPrice.textContent = '--';
    if (statusStartPrice) statusStartPrice.textContent = '--';
    if (statusMarketState) {
        statusMarketState.textContent = '--';
        statusMarketState.className = 'status-info-value';
    }
    if (statusArbitrageCount) statusArbitrageCount.textContent = '--';
    if (statusGridProfit) {
        statusGridProfit.textContent = '--';
        statusGridProfit.className = 'status-info-value';
    }
    if (statusGridProfitRate) {
        statusGridProfitRate.textContent = '--';
        statusGridProfitRate.className = 'status-info-percent';
    }
    if (statusUnrealizedProfit) {
        statusUnrealizedProfit.textContent = '--';
        statusUnrealizedProfit.className = 'status-info-value';
    }
    if (statusUnrealizedProfitRate) {
        statusUnrealizedProfitRate.textContent = '--';
        statusUnrealizedProfitRate.className = 'status-info-percent';
    }
    if (statusDirectionEl) {
        statusDirectionEl.textContent = '--';
        statusDirectionEl.className = 'status-direction';
    }
    if (statusLeverageEl) {
        statusLeverageEl.textContent = '--';
    }
    if (strategyStatusBadge) {
        strategyStatusBadge.textContent = '--';
        strategyStatusBadge.className = 'status-badge';
    }
}

// 更新UI - 无策略运行
function updateUIForNoStrategy() {
    if (strategyStatusCard) {
        strategyStatusCard.style.display = 'none';
    }

    if (startStrategyBtn) {
        startStrategyBtn.disabled = false;
    }
    if (stopStrategyBtn) {
        stopStrategyBtn.disabled = true;
    }

    // 重置策略状态卡片
    resetStrategyStatusCard();

    // 重置统计信息
    resetStatistics();
}

// 处理启动策略
async function handleStartStrategy() {
    if (!gridConfigForm) {
        showToast('配置表单未找到', 'error');
        return;
    }

    // 验证表单
    if (!gridConfigForm.checkValidity()) {
        gridConfigForm.reportValidity();
        return;
    }

    const formData = new FormData(gridConfigForm);
    
    // 获取表单值
    const symbol = formData.get('symbol').trim().toUpperCase();
    const minPrice = parseFloat(formData.get('min_price'));
    const maxPrice = parseFloat(formData.get('max_price'));
    const direction = formData.get('direction');
    const gridCount = parseInt(formData.get('grid_count'));
    const investmentAmount = parseFloat(formData.get('investment_amount'));
    const leverage = parseInt(formData.get('leverage'));
    const assetType = 'stock';  // 默认资产类型为 stock
    const marketType = formData.get('market_type') || 'contract';
    const coType = formData.get('co_type');  // 获取 coType 参数

    // 验证参数
    if (!validateGridParams({
        symbol,
        min_price: minPrice,
        max_price: maxPrice,
        grid_count: gridCount,
        investment_amount: investmentAmount,
        leverage: leverage
    })) {
        return;
    }

    // 根据网格数量计算网格间距
    // 公式：grid_spacing = 2 * (max_price - min_price) / (grid_count * (min_price + max_price))
    const avgPrice = (minPrice + maxPrice) / 2;
    const priceRange = maxPrice - minPrice;
    const gridSpacing = priceRange / (gridCount * avgPrice);

    // 构建请求参数
    const params = {
        symbol,
        min_price: minPrice,
        max_price: maxPrice,
        direction,
        grid_spacing: gridSpacing,
        investment_amount: investmentAmount,
        leverage: leverage,
        asset_type: assetType,
        market_type: marketType,
    };

    // 如果是合约市场，添加 coType 参数
    if (marketType === 'contract' && coType) {
        params.co_type = parseInt(coType, 10);
    }

    // 弹出确认对话框，确认后再真正发起启动请求
    showConfirmModal({
        title: '确认启动策略',
        message: `确定要启动 ${symbol} 的网格策略吗？`,
        onConfirm: () => doStartStrategy(params),
    });
}

// 实际执行启动策略请求的函数
async function doStartStrategy(params) {
    const { symbol } = params;

    // 启动请求发出前：启动按钮置灰并显示「启动中…」，停止按钮禁用，防止重复提交
    if (startStrategyBtn) {
        startStrategyBtn.disabled = true;
        const span = startStrategyBtn.querySelector('span');
        if (span) {
            span.textContent = '启动中...';
        }
    }
    if (stopStrategyBtn) {
        stopStrategyBtn.disabled = true;
        const span = stopStrategyBtn.querySelector('span');
        if (span) {
            span.textContent = '停止策略';
        }
    }

    try {
        const res = await fetch('/api/start', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ params }),
        });

        if (!res.ok) {
            const errorData = await res.json().catch(() => null);
            const msg = errorData?.detail || `启动策略失败: ${res.status}`;
            showToast(msg, 'error');
            // 请求失败时恢复按钮到请求前的状态：启动可点，停止置灰
            if (startStrategyBtn) {
                startStrategyBtn.disabled = false;
                const span = startStrategyBtn.querySelector('span');
                if (span) {
                    span.textContent = '启动策略';
                }
            }
            if (stopStrategyBtn) {
                stopStrategyBtn.disabled = true;
                const span = stopStrategyBtn.querySelector('span');
                if (span) {
                    span.textContent = '停止策略';
                }
            }
            return;
        }

        const data = await res.json();
        console.log('策略启动成功:', data);

        // 关闭配置模态框
        closeConfigModalHandler();

        // 立即刷新状态
        await loadStrategyStatus();

        showToast('策略启动成功！', 'success');
    } catch (error) {
        console.error('启动策略失败:', error);
        showToast('启动策略失败，请检查网络或后端服务。', 'error');
        // 异常时恢复按钮状态
        if (startStrategyBtn) {
            startStrategyBtn.disabled = false;
            const span = startStrategyBtn.querySelector('span');
            if (span) {
                span.textContent = '启动策略';
            }
        }
        if (stopStrategyBtn) {
            stopStrategyBtn.disabled = true;
            const span = stopStrategyBtn.querySelector('span');
            if (span) {
                span.textContent = '停止策略';
            }
        }
    }
}

// 处理停止策略
async function handleStopStrategy() {
    // 使用自定义确认弹窗，而不是原生 confirm，避免闪烁问题
    showConfirmModal({
        title: '确认停止策略',
        message: '确定要停止当前运行的策略吗？',
        onConfirm: () => doStopStrategy(),
    });
}

// 实际执行停止策略请求的函数
async function doStopStrategy() {
    // 点击停止后：立刻置灰停止按钮并显示「停止中…」，同时禁止启动按钮，避免重复操作
    if (stopStrategyBtn) {
        stopStrategyBtn.disabled = true;
        const span = stopStrategyBtn.querySelector('span');
        if (span) {
            span.textContent = '停止中...';
        }
    }
    if (startStrategyBtn) {
        startStrategyBtn.disabled = true;
        const span = startStrategyBtn.querySelector('span');
        if (span) {
            span.textContent = '启动策略';
        }
    }

    try {
        const res = await fetch('/api/stop', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
        });

        if (!res.ok) {
            const errorData = await res.json().catch(() => null);
            const msg = errorData?.detail || `停止策略失败: ${res.status}`;
            showToast(msg, 'error');
            // 请求失败时恢复按钮：启动按钮恢复到停止前状态（取决于当前 running 状态），停止按钮可再次点击
            if (stopStrategyBtn) {
                stopStrategyBtn.disabled = false;
                const span = stopStrategyBtn.querySelector('span');
                if (span) {
                    span.textContent = '停止策略';
                }
            }
            if (startStrategyBtn) {
                // 是否可点由当前状态决定，这里暂时恢复为可点，后续由 loadStrategyStatus 纠正
                startStrategyBtn.disabled = false;
                const span = startStrategyBtn.querySelector('span');
                if (span) {
                    span.textContent = '启动策略';
                }
            }
            return;
        }

        const data = await res.json();
        console.log('策略停止成功:', data);

        // 立即刷新状态
        await loadStrategyStatus();

        showToast('策略已停止', 'success');
    } catch (error) {
        console.error('停止策略失败:', error);
        showToast('停止策略失败，请检查网络或后端服务。', 'error');
        // 异常时恢复按钮：保留当前策略状态下的合理配置
        if (stopStrategyBtn) {
            stopStrategyBtn.disabled = false;
            const span = stopStrategyBtn.querySelector('span');
            if (span) {
                span.textContent = '停止策略';
            }
        }
        if (startStrategyBtn) {
            // 默认允许再次发起停止/启动操作，具体 running 状态由下次 /api/status 决定
            startStrategyBtn.disabled = false;
            const span = startStrategyBtn.querySelector('span');
            if (span) {
                span.textContent = '启动策略';
            }
        }
    }
}

// 在 /api/status 请求完成后，根据当前状态统一恢复按钮到互斥的稳定状态
function resetButtonsAfterStatusLoad() {
    if (!startStrategyBtn || !stopStrategyBtn) return;

    // 如果当前已经有最新的策略状态，则以此为准
    if (currentStrategyStatus && (currentStrategyStatus.running !== undefined || currentStrategyStatus.status)) {
        const isRunning = currentStrategyStatus.running || currentStrategyStatus.status === 'running';
        startStrategyBtn.disabled = isRunning;
        stopStrategyBtn.disabled = !isRunning;

        const startSpan = startStrategyBtn.querySelector('span');
        if (startSpan) {
            startSpan.textContent = '启动策略';
        }
        const stopSpan = stopStrategyBtn.querySelector('span');
        if (stopSpan) {
            stopSpan.textContent = '停止策略';
        }
        return;
    }

    // 如果没有有效状态（例如服务刚启动、策略未运行），默认视为未运行
    startStrategyBtn.disabled = false;
    stopStrategyBtn.disabled = true;

    const startSpan = startStrategyBtn.querySelector('span');
    if (startSpan) {
        startSpan.textContent = '启动策略';
    }
    const stopSpan = stopStrategyBtn.querySelector('span');
    if (stopSpan) {
        stopSpan.textContent = '停止策略';
    }
}

// 验证网格参数
function validateGridParams(params) {
    if (!params.symbol || params.symbol.trim() === '') {
        showToast('请输入交易对', 'warning');
        return false;
    }

    if (params.min_price <= 0) {
        showToast('最低价格必须大于 0', 'warning');
        return false;
    }

    if (params.max_price <= 0) {
        showToast('最高价格必须大于 0', 'warning');
        return false;
    }

    if (params.max_price <= params.min_price) {
        showToast('最高价格必须大于最低价格', 'warning');
        return false;
    }

    if (params.grid_count < 2) {
        showToast('网格数量至少为 2', 'warning');
        return false;
    }

    if (params.investment_amount <= 0) {
        showToast('投资额必须大于 0', 'warning');
        return false;
    }

    if (params.leverage === undefined || params.leverage === null) {
        showToast('杠杆倍数不能为空', 'warning');
        return false;
    }

    // 杠杆倍数范围校验：优先使用输入框自身的 min/max
    const levInput = document.getElementById('leverage');
    let minLev = 1;
    let maxLev = 100;
    if (levInput) {
        const minAttr = parseInt(levInput.min || '1', 10);
        const maxAttr = parseInt(levInput.max || '100', 10);
        if (!Number.isNaN(minAttr)) minLev = minAttr;
        if (!Number.isNaN(maxAttr)) maxLev = maxAttr;
    }

    if (params.leverage < minLev || params.leverage > maxLev) {
        showToast(`杠杆倍数必须在 ${minLev}-${maxLev} 之间`, 'warning');
        return false;
    }

    return true;
}

// 从状态更新统计信息
function updateStatisticsFromStatus(status) {
    if (!status) return;

    // 更新策略数
    if (totalStrategiesEl) {
        totalStrategiesEl.textContent = '1';
    }

    // 更新运行状态
    const isRunning = status.running || status.status === 'running';
    if (runningStrategiesEl) {
        runningStrategiesEl.textContent = isRunning ? '1' : '0';
    }
    if (stoppedStrategiesEl) {
        stoppedStrategiesEl.textContent = isRunning ? '0' : '1';
    }

    // 从 summary 对象获取统计数据
    const summary = status.summary || {};

    // 更新投入资金：使用 investment_amount（投资额，不包含杠杆）
    if (totalInvestmentEl) {
        if (status.investment_amount !== undefined && status.investment_amount !== null) {
            totalInvestmentEl.textContent = `$${formatNumber(status.investment_amount)}`;
        } else {
            totalInvestmentEl.textContent = '--';
        }
    }

    // 更新总盈亏：使用 summary.total_pnl
    if (totalPnLEl) {
        const pnl = summary.total_pnl || 0;
        const pnlClass = pnl >= 0 ? 'positive' : 'negative';
        const pnlPrefix = pnl >= 0 ? '+' : '-';
        totalPnLEl.textContent = `${pnlPrefix}$${formatNumber(Math.abs(pnl))}`;
        totalPnLEl.className = `metric-value ${pnlClass}`;
    }

    // 更新成交额：使用 summary.total_volume
    if (totalVolumeEl) {
        if (summary.total_volume !== undefined && summary.total_volume !== null) {
            totalVolumeEl.textContent = `$${formatNumber(summary.total_volume)}`;
        } else {
            totalVolumeEl.textContent = '--';
        }
    }

    // 更新收益率：使用 summary.annualized_return
    if (avgReturnEl) {
        if (summary.annualized_return !== undefined && summary.annualized_return !== null) {
            const returnRate = summary.annualized_return;
            const returnClass = returnRate >= 0 ? 'positive' : 'negative';
            const returnPrefix = returnRate >= 0 ? '+' : '-';
            avgReturnEl.textContent = `${returnPrefix}${formatNumber(Math.abs(returnRate))}%`;
            avgReturnEl.className = `metric-value ${returnClass}`;
        } else {
            avgReturnEl.textContent = '--';
            avgReturnEl.className = 'metric-value';
        }
    }
}

// 重置统计信息
function resetStatistics() {
    if (totalStrategiesEl) {
        totalStrategiesEl.textContent = '0';
    }
    if (runningStrategiesEl) {
        runningStrategiesEl.textContent = '0';
    }
    if (stoppedStrategiesEl) {
        stoppedStrategiesEl.textContent = '0';
    }
    if (totalInvestmentEl) {
        totalInvestmentEl.textContent = '--';
    }
    if (totalPnLEl) {
        totalPnLEl.textContent = '--';
        totalPnLEl.className = 'metric-value';
    }
    if (totalVolumeEl) {
        totalVolumeEl.textContent = '--';
    }
    if (avgReturnEl) {
        avgReturnEl.textContent = '--';
        avgReturnEl.className = 'metric-value';
    }
}

// 工具函数
function formatNumber(num) {
    if (num === null || num === undefined || isNaN(num)) {
        return '0.00';
    }
    if (num >= 1000) {
        return num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    return num.toFixed(2);
}

// 清理定时器
window.addEventListener('beforeunload', () => {
    if (statusUpdateInterval) {
        clearInterval(statusUpdateInterval);
    }
});
