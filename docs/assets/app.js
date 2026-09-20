let watchlist = JSON.parse(localStorage.getItem('cyber_stock_watchlist') || '[]');

document.addEventListener('DOMContentLoaded', () => {
    updateWatchCount();
    setupEventDelegation();
});

function updateWatchCount() {
    document.querySelectorAll('.watch-count').forEach(el => { el.textContent = watchlist.length; });
}

function showToast(message) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = message;
    toast.style.display = 'block';
    setTimeout(() => { toast.style.display = 'none'; }, 3000);
}

function setupEventDelegation() {
    document.body.addEventListener('click', (e) => {
        const watchBtn = e.target.closest('.watch-btn');
        if (watchBtn) {
            toggleWatchlist(watchBtn.dataset.code, watchBtn.dataset.name);
            return;
        }
        const openBtn = e.target.closest('.open-watchlist-btn');
        if (openBtn) {
            renderWatchlistModal();
            document.getElementById('watchlist-modal').classList.add('open');
            return;
        }
        const closeBtn = e.target.closest('.modal-close, .modal-overlay');
        if (closeBtn && (e.target.classList.contains('modal-close') || e.target.classList.contains('modal-overlay'))) {
            document.getElementById('watchlist-modal').classList.remove('open');
            return;
        }
        const removeBtn = e.target.closest('.remove-btn');
        if (removeBtn) {
            removeFromWatchlist(removeBtn.dataset.code);
            return;
        }
    });
}

function toggleWatchlist(code, name) {
    code = code.toUpperCase();
    const index = watchlist.findIndex(item => item.code === code);
    if (index >= 0) {
        watchlist.splice(index, 1);
        showToast(`[ ${code} ] を監視リストから解除しました`);
    } else {
        watchlist.push({ code, name });
        showToast(`⭐ [ ${code} ] ${name} を監視リストに登録しました！`);
    }
    localStorage.setItem('cyber_stock_watchlist', JSON.stringify(watchlist));
    updateWatchCount();
    renderWatchlistModal();
}

function removeFromWatchlist(code) {
    watchlist = watchlist.filter(item => item.code !== code);
    localStorage.setItem('cyber_stock_watchlist', JSON.stringify(watchlist));
    updateWatchCount();
    renderWatchlistModal();
}

function renderWatchlistModal() {
    const listEl = document.getElementById('watchlist-items');
    if (!listEl) return;

    if (watchlist.length === 0) {
        listEl.innerHTML = '<li class="watchlist-empty">監視銘柄はまだ登録されていません</li>';
        return;
    }

    listEl.innerHTML = watchlist.map(item => `
        <li>
            <a href="https://finance.yahoo.co.jp/quote/${item.code}.T" target="_blank">[ ${item.code} ] ${item.name}</a>
            <button class="remove-btn" data-code="${item.code}">解除</button>
        </li>
    `).join('');
}