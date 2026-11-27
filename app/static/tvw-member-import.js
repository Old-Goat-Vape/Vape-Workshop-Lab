const importForm = document.getElementById('member-import-form');
const importFile = document.getElementById('member-import-file');
const importPreview = document.getElementById('member-import-preview');

importFile.addEventListener('change', () => importPreview.replaceChildren());

importForm.addEventListener('submit', async (event) => {
    if (event.submitter?.value !== 'preview') return;

    event.preventDefault();
    const file = importFile.files[0];
    const fields = new FormData(importForm);
    fields.set('action', 'preview');
    event.submitter.disabled = true;
    importPreview.textContent = 'Checking CSV…';

    try {
        const response = await fetch(importForm.action, {
            method: 'POST',
            body: fields,
            headers: {'X-TVW-Import-Preview': '1'},
            redirect: 'error',
        });
        if (!response.ok && response.status !== 400) throw new Error();
        const html = await response.text();
        if (importFile.files[0] === file) importPreview.innerHTML = html;
    } catch {
        if (importFile.files[0] === file) {
            importPreview.textContent = 'The preview could not load. Try again or sign in again if your session expired.';
        }
    } finally {
        event.submitter.disabled = false;
    }
});
