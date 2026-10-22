import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { VqaStudio } from '../../../../src/views/VqaStudio';
import { fetchWithIdentity } from '../../../../src/app/utils';

vi.mock('react-redux', () => ({ useDispatch: () => vi.fn(), useSelector: (selector: (state: unknown) => unknown) => selector({ activeWorkspace: { id: 'vqa-test', readOnly: false } }) }));
vi.mock('../../../../src/app/dfSlice', () => ({ dfActions: { resetForNewWorkspace: vi.fn() } }));
vi.mock('../../../../src/app/utils', () => ({ fetchWithIdentity: vi.fn() }));
const item = { id: 'a'.repeat(32), image_sha256: 'b'.repeat(64), question: 'What color?', answer: 'red', answer_mode: 'exact',
    answer_label: null, choices: [], aliases: [], context: '', split: 'validation', revision: 1 };
const version = { id: 'c'.repeat(64), manifest: { split: 'validation', items: [item] } };
const run = { id: 'run', version: version.id, status: 'running', model: 'local', scorer: 'mode-exact-v2', error: null, rankable: false,
    metrics: { total: 1, completed: 0, scored: 0, correct: 0, manual: 0, accuracy: null }, results: [], cache_hits: 0 };
let started = false;

beforeEach(() => {
    vi.clearAllMocks(); started = false;
    vi.mocked(fetchWithIdentity).mockImplementation(async (url, options) => {
        const route = String(url);
        if (route.startsWith('/api/vqa/images/')) return new Response(new Blob());
        if (options?.method === 'POST') {
            const headers = new Headers(options.headers);
            if (headers.get('Content-Type') !== 'application/json')
                return new Response(JSON.stringify({ status: 'error', error: { code: 'VALIDATION_ERROR', message: 'Expected a JSON object.' } }));
        }
        let data: unknown = {};
        if (route === '/api/sessions/list') data = { sessions: [] };
        if (route === '/api/vqa/state') data = { items: [item], versions: [version], runs: started ? [run] : [] };
        if (route === '/api/vqa/items') data = { ...item, ...JSON.parse(String(options?.body)) };
        if (route === '/api/vqa/versions') data = version;
        if (route === '/api/vqa/runs') { started = true; data = run; }
        return new Response(JSON.stringify({ status: 'success', data }));
    });
});

describe('VQA studio production request boundary', () => {
    it('sends workspace-scoped JSON for creation, annotation, freeze, run and cancel', async () => {
        render(<MemoryRouter><VqaStudio /></MemoryRouter>);
        fireEvent.click(await screen.findByRole('button', { name: 'red' }));
        fireEvent.change(screen.getByLabelText('Question'), { target: { value: 'Which color?' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save annotation revision' }));
        expect(await screen.findByText('Annotation saved. Existing frozen versions remain unchanged.')).toBeVisible();
        fireEvent.click(screen.getByRole('button', { name: 'Freeze version' }));
        await screen.findByText(/Frozen version/);
        fireEvent.click(screen.getByRole('tab', { name: 'Experiments' }));
        fireEvent.click(screen.getByRole('button', { name: 'Run evaluation' }));
        fireEvent.click(await screen.findByRole('button', { name: 'Cancel active run' }));
        await waitFor(() => expect(vi.mocked(fetchWithIdentity).mock.calls.some(([url]) => url === '/api/vqa/runs/run/cancel')).toBe(true));
        const writes = vi.mocked(fetchWithIdentity).mock.calls.filter(([, options]) => options?.method === 'POST');
        expect(writes.map(([url]) => url)).toEqual(expect.arrayContaining(['/api/sessions/create', '/api/vqa/items', '/api/vqa/versions', '/api/vqa/runs', '/api/vqa/runs/run/cancel']));
        for (const [, options] of writes) {
            expect(new Headers(options?.headers).get('Content-Type')).toBe('application/json');
            expect(new Headers(options?.headers).get('X-Workspace-Id')).toBe('vqa-test');
        }
    });

    it('derives readable input and tab text from the dark MUI palette', async () => {
        render(<MemoryRouter><VqaStudio /></MemoryRouter>);
        await screen.findByRole('button', { name: 'red' });
        expect(getComputedStyle(screen.getByLabelText('Question')).color).toBe('rgb(255, 255, 255)');
        expect(getComputedStyle(screen.getByRole('tab', { name: 'Experiments' })).color).not.toBe('rgba(0, 0, 0, 0.6)');
    });
});
