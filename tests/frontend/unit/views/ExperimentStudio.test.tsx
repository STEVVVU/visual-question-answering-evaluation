import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ExperimentStudio } from '../../../../src/views/ExperimentStudio';
import { apiRequest } from '../../../../src/app/apiClient';

const { state, dispatch } = vi.hoisted(() => ({
    state: { activeWorkspace: { id: 'workspace-a', displayName: 'Analysis', readOnly: false } as { id: string; displayName: string; readOnly: boolean } | null },
    dispatch: vi.fn(),
}));
vi.mock('react-redux', () => ({
    useDispatch: () => dispatch,
    useSelector: (selector: (value: typeof state) => unknown) => selector(state),
}));
vi.mock('../../../../src/app/apiClient', () => ({ apiRequest: vi.fn() }));
vi.mock('../../../../src/app/dfSlice', () => ({ dfActions: { resetForNewWorkspace: vi.fn() } }));

const table = { name: 'experiment', row_count: 120,
    columns: ['unit', 'arm', 'pre', 'outcome'].map(name => ({ name })) };
const estimate = { effect: 2, control_mean: 50, treatment_mean: 52, standard_error: 0.5,
    confidence_interval: [1, 3], p_value: 0.001, degrees_of_freedom: 118 };
const report = { status: 'ready', method_version: 'trialdesk-cuped-1', selected_input_sha256: 'a'.repeat(64),
    counts: { control: 60, treatment: 60, total: 120 }, sample_ratio_p_value: 1,
    diagnostics: [], assumptions: ['Fixed horizon'], theta: 0.5,
    estimated_effect_variance_reduction: 0.5, estimates: { ordinary: estimate, cuped: estimate } };

async function chooseAndConfirm() {
    const selector = await screen.findByLabelText('Workspace table');
    await waitFor(() => expect(selector).not.toBeDisabled());
    fireEvent.mouseDown(selector);
    fireEvent.click(await screen.findByRole('option', { name: 'experiment · 120 rows' }));
    for (const checkbox of screen.getAllByRole('checkbox')) fireEvent.click(checkbox);
}

beforeEach(() => {
    vi.clearAllMocks();
    state.activeWorkspace = { id: 'workspace-a', displayName: 'Analysis', readOnly: false };
    vi.mocked(apiRequest).mockImplementation(async url => url === '/api/tables/list-tables'
        ? { data: { tables: [table] } } : { data: report });
});

describe('Experiment studio', () => {
    it('requires declarations and binds analysis to its displayed workspace', async () => {
        render(<ExperimentStudio />);
        expect(screen.getByRole('button', { name: 'Validate and analyze' })).toBeDisabled();
        await chooseAndConfirm();
        fireEvent.click(screen.getByRole('button', { name: 'Validate and analyze' }));
        expect(await screen.findByRole('table', { name: 'Treatment effect estimates' })).toBeVisible();
        const call = vi.mocked(apiRequest).mock.calls.find(([url]) => url === '/api/experiments/analyze');
        expect(call?.[1]?.headers).toMatchObject({ 'X-Workspace-Id': 'workspace-a' });
        expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ table: 'experiment', pre_period_confirmed: true });
        fireEvent.change(screen.getByLabelText('Control label'), { target: { value: 'changed' } });
        expect(screen.queryByRole('table', { name: 'Treatment effect estimates' })).not.toBeInTheDocument();
    });

    it('discards a late report after the workspace changes', async () => {
        let resolve: (value: { data: typeof report }) => void = () => {};
        vi.mocked(apiRequest).mockImplementation(url => url === '/api/tables/list-tables'
            ? Promise.resolve({ data: { tables: [table] } })
            : new Promise(done => { resolve = done; }));
        const view = render(<ExperimentStudio />);
        await chooseAndConfirm();
        fireEvent.click(screen.getByRole('button', { name: 'Validate and analyze' }));
        state.activeWorkspace = { id: 'workspace-b', displayName: 'Other', readOnly: false };
        view.rerender(<ExperimentStudio />);
        resolve({ data: report });
        await waitFor(() => expect(screen.getByLabelText('Workspace table')).not.toBeDisabled());
        expect(screen.queryByRole('table', { name: 'Treatment effect estimates' })).not.toBeInTheDocument();
    });

    it('shows blocked diagnostics without treatment-effect results', async () => {
        vi.mocked(apiRequest).mockImplementation(async url => url === '/api/tables/list-tables'
            ? { data: { tables: [table] } }
            : { data: { ...report, status: 'blocked', estimates: null,
                diagnostics: [{ code: 'SAMPLE_RATIO_MISMATCH', severity: 'error', message: 'Allocation failed' }] } });
        render(<ExperimentStudio />);
        await chooseAndConfirm();
        fireEvent.click(screen.getByRole('button', { name: 'Validate and analyze' }));
        expect(await screen.findByText('Allocation failed')).toBeVisible();
        expect(screen.queryByRole('table', { name: 'Treatment effect estimates' })).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Export analysis JSON' })).toBeEnabled();
    });
});
