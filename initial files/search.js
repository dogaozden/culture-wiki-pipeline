/**
 * Culture Wiki Search
 * Client-side fuzzy search using the pre-generated search_index.json
 */

let searchIndex = [];
let searchInput = null;
let searchResults = null;

// Fetch and initialize search index
async function initSearch() {
    searchInput = document.getElementById('search-input');
    if (!searchInput) return;

    // Create results container
    searchResults = document.createElement('div');
    searchResults.id = 'search-results';
    searchResults.className = 'search-results';
    searchInput.parentNode.appendChild(searchResults);

    // Determine root path from current page
    const rootPath = getRootPath();

    try {
        const response = await fetch(rootPath + 'search_index.json');
        if (response.ok) {
            searchIndex = await response.json();
            console.log(`Search index loaded with ${searchIndex.length} entries`);
        }
    } catch (e) {
        console.warn('Could not load search index:', e);
    }

    // Set up event listeners
    searchInput.addEventListener('input', handleSearch);
    searchInput.addEventListener('focus', handleSearch);
    document.addEventListener('click', (e) => {
        if (!searchInput.contains(e.target) && !searchResults.contains(e.target)) {
            searchResults.classList.remove('active');
        }
    });
}

function getRootPath() {
    // Find a link to css/style.css and extract root path
    const cssLink = document.querySelector('link[href*="css/style.css"]');
    if (cssLink) {
        const href = cssLink.getAttribute('href');
        return href.replace('css/style.css', '');
    }
    return '';
}

function handleSearch() {
    const query = searchInput.value.trim().toLowerCase();

    if (query.length < 2) {
        searchResults.classList.remove('active');
        searchResults.innerHTML = '';
        return;
    }

    const matches = searchIndex
        .filter(entry => {
            const nameMatch = entry.name.toLowerCase().includes(query);
            const aliasMatch = entry.aliases && entry.aliases.some(a => a.toLowerCase().includes(query));
            return nameMatch || aliasMatch;
        })
        .slice(0, 10);  // Limit to 10 results

    if (matches.length === 0) {
        searchResults.innerHTML = '<div class="search-no-results">No results found</div>';
        searchResults.classList.add('active');
        return;
    }

    const rootPath = getRootPath();
    searchResults.innerHTML = matches.map(entry => {
        const typeLabel = entry.type.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        return `
            <a href="${rootPath}${entry.url}" class="search-result-item">
                <div class="search-result-name">${escapeHtml(entry.name)}</div>
                <div class="search-result-meta">
                    <span class="search-result-type">${typeLabel}</span>
                </div>
            </a>
        `;
    }).join('');

    searchResults.classList.add('active');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Initialize on DOM ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initSearch);
} else {
    initSearch();
}
