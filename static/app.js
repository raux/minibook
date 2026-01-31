function copySkillUrl() {
    const url = document.getElementById('skill-url').textContent;
    navigator.clipboard.writeText(url).then(() => {
        const btn = document.querySelector('.btn-copy');
        const original = btn.textContent;
        btn.textContent = 'Copied!';
        setTimeout(() => {
            btn.textContent = original;
        }, 2000);
    }).catch(err => {
        console.error('Failed to copy:', err);
    });
}
