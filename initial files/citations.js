/**
 * Culture Wiki — Citation System
 * Handles hover/click tooltips for inline citations
 */

(function () {
    'use strict';

    // ==========================================================================
    // Configuration
    // ==========================================================================

    const CONFIG = {
        tooltipOffset: 12,
        hoverDelay: 200,
        mobileBreakpoint: 900
    };

    // ==========================================================================
    // State
    // ==========================================================================

    let citations = [];
    let tooltip = null;
    let hoverTimeout = null;
    let activeLink = null;

    // ==========================================================================
    // Initialization
    // ==========================================================================

    function init() {
        loadCitations();
        tooltip = document.getElementById('citation-tooltip');

        if (!tooltip) {
            console.warn('Citation tooltip element not found');
            return;
        }

        bindCitationEvents();
        bindTooltipEvents();
        bindThemeToggle();
        bindSearch();
    }

    function loadCitations() {
        const dataElement = document.getElementById('citations-data');
        if (dataElement) {
            try {
                citations = JSON.parse(dataElement.textContent);
            } catch (e) {
                console.error('Failed to parse citations data:', e);
                citations = [];
            }
        }
    }

    // ==========================================================================
    // Citation Events
    // ==========================================================================

    function bindCitationEvents() {
        document.querySelectorAll('.citation-link').forEach(link => {
            const citationId = parseInt(link.dataset.citationId, 10);

            // Desktop: hover behavior
            link.addEventListener('mouseenter', (e) => {
                hoverTimeout = setTimeout(() => {
                    showTooltip(citationId, e.target);
                }, CONFIG.hoverDelay);
            });

            link.addEventListener('mouseleave', () => {
                clearTimeout(hoverTimeout);
                // Don't hide if hovering over tooltip itself
                setTimeout(() => {
                    if (!tooltip.matches(':hover') && activeLink !== link) {
                        hideTooltip();
                    }
                }, 100);
            });

            // Mobile/Click: show tooltip and scroll to reference
            link.addEventListener('click', (e) => {
                e.preventDefault();

                // Show tooltip
                showTooltip(citationId, e.target);
                activeLink = link;

                // Scroll to the reference after a short delay
                setTimeout(() => {
                    const refElement = document.getElementById(`cite-${citationId}`);
                    if (refElement) {
                        refElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
                        // Highlight the reference briefly
                        refElement.classList.add('highlight');
                        setTimeout(() => refElement.classList.remove('highlight'), 2000);
                    }
                }, 100);
            });
        });
    }

    function bindTooltipEvents() {
        // Keep tooltip visible when hovering over it
        tooltip.addEventListener('mouseenter', () => {
            clearTimeout(hoverTimeout);
        });

        tooltip.addEventListener('mouseleave', () => {
            if (!activeLink) {
                hideTooltip();
            }
        });

        // Close button
        const closeBtn = tooltip.querySelector('.citation-close');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => {
                hideTooltip();
                activeLink = null;
            });
        }

        // Close on escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                hideTooltip();
                activeLink = null;
            }
        });

        // Close when clicking outside
        document.addEventListener('click', (e) => {
            if (!tooltip.contains(e.target) &&
                !e.target.classList.contains('citation-link')) {
                hideTooltip();
                activeLink = null;
            }
        });
    }

    // ==========================================================================
    // Tooltip Display
    // ==========================================================================

    function showTooltip(citationId, anchor) {
        const citation = citations.find(c => c.id === citationId);

        if (!citation) {
            console.warn('Citation not found:', citationId);
            return;
        }

        // Populate content
        tooltip.querySelector('.citation-book').textContent = citation.book;
        tooltip.querySelector('.citation-quote').textContent = `"${citation.quote}"`;

        // Position tooltip
        positionTooltip(anchor);

        // Show with animation
        tooltip.classList.add('visible');
    }

    function hideTooltip() {
        tooltip.classList.remove('visible');
    }

    function positionTooltip(anchor) {
        const anchorRect = anchor.getBoundingClientRect();
        const tooltipRect = tooltip.getBoundingClientRect();
        const viewportWidth = window.innerWidth;
        const viewportHeight = window.innerHeight;

        // Default: position below and centered on anchor
        let top = anchorRect.bottom + CONFIG.tooltipOffset;
        let left = anchorRect.left + (anchorRect.width / 2) - (tooltipRect.width / 2);

        // Adjust if tooltip would overflow right edge
        if (left + tooltipRect.width > viewportWidth - CONFIG.tooltipOffset) {
            left = viewportWidth - tooltipRect.width - CONFIG.tooltipOffset;
        }

        // Adjust if tooltip would overflow left edge
        if (left < CONFIG.tooltipOffset) {
            left = CONFIG.tooltipOffset;
        }

        // If tooltip would overflow bottom, show above anchor instead
        if (top + tooltipRect.height > viewportHeight - CONFIG.tooltipOffset) {
            top = anchorRect.top - tooltipRect.height - CONFIG.tooltipOffset;
        }

        // Apply position
        tooltip.style.top = `${top}px`;
        tooltip.style.left = `${left}px`;
    }

    // ==========================================================================
    // Theme Toggle
    // ==========================================================================

    function bindThemeToggle() {
        const toggle = document.getElementById('theme-toggle');
        if (!toggle) return;

        // Check for saved preference
        const savedTheme = localStorage.getItem('culture-wiki-theme');
        if (savedTheme) {
            document.documentElement.setAttribute('data-theme', savedTheme);
        }

        toggle.addEventListener('click', () => {
            const currentTheme = document.documentElement.getAttribute('data-theme');
            const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

            document.documentElement.setAttribute('data-theme', newTheme);
            localStorage.setItem('culture-wiki-theme', newTheme);
        });
    }

    // ==========================================================================
    // Search (Basic client-side filtering for small wikis)
    // ==========================================================================

    function bindSearch() {
        const searchInput = document.getElementById('search-input');
        if (!searchInput) return;

        // This is a placeholder for client-side search
        // For a full implementation, you'd want to load a search index
        searchInput.addEventListener('input', (e) => {
            const query = e.target.value.toLowerCase().trim();

            if (query.length < 2) {
                // Clear results / reset
                return;
            }

            // TODO: Implement search functionality
            // For now, just log
            console.log('Search query:', query);
        });
    }

    // ==========================================================================
    // Utility: Convert citation markers [N] to clickable links
    // This would typically be done server-side in Python, but here's a JS version
    // ==========================================================================

    function processCitationMarkers() {
        document.querySelectorAll('.section-content').forEach(section => {
            section.innerHTML = section.innerHTML.replace(
                /\[(\d+)\]/g,
                (match, num) => {
                    return `<a href="#cite-${num}" class="citation-link" data-citation-id="${num}">[${num}]</a>`;
                }
            );
        });
    }

    // ==========================================================================
    // Mobile Menu (if needed)
    // ==========================================================================

    function bindMobileMenu() {
        // Add a hamburger button for mobile if sidebar is hidden
        if (window.innerWidth <= CONFIG.mobileBreakpoint) {
            // Create mobile menu button if it doesn't exist
            let menuBtn = document.getElementById('mobile-menu-btn');
            if (!menuBtn) {
                menuBtn = document.createElement('button');
                menuBtn.id = 'mobile-menu-btn';
                menuBtn.className = 'mobile-menu-btn';
                menuBtn.innerHTML = '☰';
                menuBtn.setAttribute('aria-label', 'Open menu');
                document.body.appendChild(menuBtn);

                menuBtn.addEventListener('click', () => {
                    document.querySelector('.sidebar').classList.toggle('open');
                });
            }
        }
    }

    // ==========================================================================
    // Initialize on DOM ready
    // ==========================================================================

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
