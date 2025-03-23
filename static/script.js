// Main JavaScript for Web Radio Recorder

document.addEventListener('DOMContentLoaded', function() {
    // Handle form validation
    const forms = document.querySelectorAll('.needs-validation');
    
    Array.from(forms).forEach(form => {
        form.addEventListener('submit', event => {
            if (!form.checkValidity()) {
                event.preventDefault();
                event.stopPropagation();
            }
            
            form.classList.add('was-validated');
        }, false);
    });
    
    // Initialize tooltips if Bootstrap is available
    if (typeof bootstrap !== 'undefined' && bootstrap.Tooltip) {
        const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
        tooltipTriggerList.map(function (tooltipTriggerEl) {
            return new bootstrap.Tooltip(tooltipTriggerEl);
        });
    }
    
    // Update relative times for timestamps
    function updateRelativeTimes() {
        const timeElements = document.querySelectorAll('.relative-time');
        const now = new Date();
        
        timeElements.forEach(el => {
            const timestamp = new Date(el.dataset.time);
            const diffMs = timestamp - now;
            
            if (isNaN(diffMs)) return;
            
            if (diffMs < 0) {
                el.textContent = 'overdue';
                el.classList.add('text-danger');
            } else {
                const diffMinutes = Math.floor(diffMs / 60000);
                if (diffMinutes < 60) {
                    el.textContent = `in ${diffMinutes} minute${diffMinutes !== 1 ? 's' : ''}`;
                } else {
                    const diffHours = Math.floor(diffMinutes / 60);
                    const remainingMinutes = diffMinutes % 60;
                    if (diffHours < 24) {
                        el.textContent = `in ${diffHours} hour${diffHours !== 1 ? 's' : ''} ${remainingMinutes} minute${remainingMinutes !== 1 ? 's' : ''}`;
                    } else {
                        const diffDays = Math.floor(diffHours / 24);
                        el.textContent = `in ${diffDays} day${diffDays !== 1 ? 's' : ''} ${diffHours % 24} hour${diffHours % 24 !== 1 ? 's' : ''}`;
                    }
                }
                
                // Add color coding
                if (diffMinutes < 15) {
                    el.classList.add('text-danger');
                } else if (diffMinutes < 60) {
                    el.classList.add('text-warning');
                } else {
                    el.classList.add('text-success');
                }
            }
        });
    }
    
    // Update times initially and then every minute
    if (document.querySelector('.relative-time')) {
        updateRelativeTimes();
        setInterval(updateRelativeTimes, 60000);
    }
});
