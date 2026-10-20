import React, { useEffect, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import {
    Alert, Box, Button, Checkbox, Chip, FormControlLabel, LinearProgress, MenuItem,
    Paper, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
    TextField, Typography,
} from '@mui/material';
import { apiRequest } from '../app/apiClient';
import { DataFormulatorState, dfActions } from '../app/dfSlice';
import { generateUUID } from '../app/identity';
import { borderColor, radius } from '../app/tokens';
import exampleUrl from '../../tools/fixtures/synthetic-experiment.csv?url';

type TableInfo = { name: string; columns: { name: string }[]; row_count: number };
type Mapping = {
    table: string; unit_column: string; variant_column: string;
    outcome_column: string; covariate_column: string; control: string; treatment: string;
    expected_treatment_fraction: number; randomized_units_confirmed: boolean;
    pre_period_confirmed: boolean; fixed_horizon_confirmed: boolean;
};
type Estimate = {
    effect: number; control_mean: number; treatment_mean: number; standard_error: number;
    confidence_interval: [number, number]; p_value: number; degrees_of_freedom: number;
};
type ReportBase = {
    method_version: string; selected_input_sha256: string; specification: Mapping;
    counts: { control: number; treatment: number; total: number }; sample_ratio_p_value: number;
    diagnostics: { code: string; severity: 'error' | 'warning'; message: string }[];
    assumptions: string[];
};
type Report = ReportBase & (
    { status: 'blocked'; estimates: null }
    | { status: 'ready'; estimates: { ordinary: Estimate; cuped: Estimate };
        theta: number; estimated_effect_variance_reduction: number }
);
type RunState = { status: 'idle' | 'loading' | 'uploading' | 'analyzing' }
    | { status: 'error'; message: string }
    | { status: 'result'; report: Report };

const initialMapping: Mapping = {
    table: '', unit_column: '', variant_column: '', outcome_column: '', covariate_column: '',
    control: 'control', treatment: 'treatment', expected_treatment_fraction: 0.5,
    randomized_units_confirmed: false, pre_period_confirmed: false, fixed_horizon_confirmed: false,
};
const columnFields = [
    ['unit_column', 'Independent unit ID'], ['variant_column', 'Variant'],
    ['outcome_column', 'Outcome metric'], ['covariate_column', 'Pre-period metric'],
] as const;
const declarations = [
    ['randomized_units_confirmed', 'Units were randomized independently, with one row per unit.'],
    ['pre_period_confirmed', 'The covariate was measured before treatment, without treatment leakage.'],
    ['fixed_horizon_confirmed', 'This is one preselected outcome at a fixed analysis horizon.'],
] as const;
const format = (value: number) => value !== 0 && (Math.abs(value) < 0.0001 || Math.abs(value) >= 1e6)
    ? value.toExponential(3) : new Intl.NumberFormat('en', { maximumSignificantDigits: 5 }).format(value);
const panel = { p: { xs: 2, md: 3 }, border: `1px solid ${borderColor.view}`, borderRadius: radius.md };

export function ExperimentStudio() {
    const dispatch = useDispatch();
    const workspace = useSelector((state: DataFormulatorState) => state.activeWorkspace);
    return <Box component="main" sx={{ overflow: 'auto', bgcolor: 'background.default', p: { xs: 2, md: 4 } }}>
        <Box sx={{ maxWidth: 1200, mx: 'auto' }}>
            <Stack direction="row" spacing={2} sx={{ alignItems: 'center', flexWrap: 'wrap', mb: 1 }}>
                <Typography variant="h4" component="h1">Experiment studio</Typography>
                <Chip label="No model required" size="small" variant="outlined" />
            </Stack>
            <Typography color="text.secondary" sx={{ mb: 3, maxWidth: 820 }}>
                Validate a two-arm experiment. Compare ordinary and CUPED estimates in the outcome's units,
                inspect allocation checks, and export the result with its input fingerprint.
            </Typography>
            {workspace ? <StudioBody key={workspace.id} workspaceId={workspace.id} readOnly={!!workspace.readOnly} /> :
                <Paper variant="outlined" sx={panel}>
                    <Typography component="h2" variant="h6" gutterBottom>Start with a local workspace</Typography>
                    <Typography sx={{ mb: 2 }}>Upload your unit-level CSV or load the clearly labeled synthetic example. Existing sessions remain available in App.</Typography>
                    <Button variant="contained" onClick={() => dispatch(dfActions.resetForNewWorkspace({
                        id: generateUUID(), displayName: 'Experiment workspace',
                    }))}>Create experiment workspace</Button>
                </Paper>}
        </Box>
    </Box>;
}

function StudioBody({ workspaceId, readOnly }: { workspaceId: string; readOnly: boolean }) {
    const [tables, setTables] = useState<TableInfo[]>([]);
    const [mapping, setMapping] = useState<Mapping>(initialMapping);
    const [run, setRun] = useState<RunState>({ status: 'loading' });
    const alive = useRef(true);
    const busy = ['loading', 'uploading', 'analyzing'].includes(run.status);
    const headers = { 'X-Workspace-Id': workspaceId };

    useEffect(() => {
        alive.current = true;
        const controller = new AbortController();
        apiRequest<{ tables: TableInfo[] }>('/api/tables/list-tables', {
            headers: { 'X-Workspace-Id': workspaceId }, signal: controller.signal,
        }).then(({ data }) => {
            if (alive.current) { setTables(data.tables); setRun({ status: 'idle' }); }
        }).catch(error => {
            if (alive.current && !controller.signal.aborted) setRun({ status: 'error', message: String(error) });
        });
        return () => { alive.current = false; controller.abort(); };
    }, [workspaceId]);

    function change<K extends keyof Mapping>(key: K, value: Mapping[K]) {
        setMapping(previous => ({ ...previous, [key]: value }));
        setRun({ status: 'idle' });
    }

    function chooseTable(name: string, available = tables) {
        const names = available.find(table => table.name === name)?.columns.map(column => column.name) ?? [];
        const find = (...options: string[]) => options.find(name => names.includes(name)) ?? '';
        setMapping({ ...initialMapping, table: name,
            unit_column: find('unit_id', 'unit'), variant_column: find('variant', 'arm'),
            outcome_column: find('outcome'), covariate_column: find('pre_period', 'pre') });
        setRun({ status: 'idle' });
    }

    async function upload(file: File | Blob, filename: string) {
        setRun({ status: 'uploading' });
        try {
            if (file.size > 10 * 1024 * 1024) throw new Error('Use a CSV of at most 10 MiB for this upload.');
            const form = new FormData();
            form.append('file', file, filename);
            form.append('table_name', `experiment_${generateUUID().replaceAll('-', '').slice(0, 12)}`);
            const saved = await apiRequest<{ table_name: string }>('/api/tables/create-table', {
                method: 'POST', headers, body: form,
            });
            const listed = await apiRequest<{ tables: TableInfo[] }>('/api/tables/list-tables', { headers });
            if (!alive.current) return;
            setTables(listed.data.tables);
            chooseTable(saved.data.table_name, listed.data.tables);
        } catch (error) {
            if (alive.current) setRun({ status: 'error', message: String(error) });
        }
    }

    async function loadExample() {
        setRun({ status: 'uploading' });
        try {
            const response = await fetch(exampleUrl);
            if (!response.ok) throw new Error('The bundled synthetic example could not be read.');
            const file = await response.blob();
            if (alive.current) await upload(file, 'synthetic-experiment.csv');
        } catch (error) {
            if (alive.current) setRun({ status: 'error', message: String(error) });
        }
    }

    async function analyze() {
        setRun({ status: 'analyzing' });
        try {
            const { data } = await apiRequest<Report>('/api/experiments/analyze', {
                method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' },
                body: JSON.stringify(mapping),
            });
            if (alive.current) setRun({ status: 'result', report: data });
        } catch (error) {
            if (alive.current) setRun({ status: 'error', message: String(error) });
        }
    }

    const selectedTable = tables.find(table => table.name === mapping.table);
    const canAnalyze = !!selectedTable && columnFields.every(([key]) => mapping[key])
        && declarations.every(([key]) => mapping[key]);

    return <Stack spacing={3}>
        <Paper variant="outlined" sx={panel}>
            <Typography component="h2" variant="h6" gutterBottom>1. Choose the data</Typography>
            <Stack direction={{ xs: 'column', sm: 'row' }} gap={1} sx={{ mb: 2 }}>
                <Button component="label" variant="outlined" disabled={busy || readOnly}>
                    Upload experiment CSV
                    <input hidden type="file" accept=".csv" aria-label="Experiment CSV" onChange={event => {
                        const file = event.target.files?.[0];
                        event.target.value = '';
                        if (file) void upload(file, file.name);
                    }} />
                </Button>
                <Button variant="text" disabled={busy || readOnly} onClick={loadExample}>Load synthetic example</Button>
            </Stack>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                The example contains 1,000 generated units with a known +2 treatment effect. It contains no real user observations.
                Uploads create a new table; existing tables are not replaced.
            </Typography>
            <TextField select fullWidth label="Workspace table" value={mapping.table} disabled={busy}
                onChange={event => chooseTable(event.target.value)}>
                {tables.map(table => <MenuItem key={table.name} value={table.name}>{table.name} · {table.row_count.toLocaleString()} rows</MenuItem>)}
            </TextField>
            {readOnly && <Alert severity="info" sx={{ mt: 2 }}>This workspace is read-only. Existing tables can still be analyzed.</Alert>}
        </Paper>
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1.1fr 1fr' }, gap: 3 }}>
            <Paper variant="outlined" sx={panel}>
                <Typography component="h2" variant="h6" gutterBottom>2. Declare the design</Typography>
                <Stack spacing={2}>
                    {columnFields.map(([key, label]) => <TextField key={key} select fullWidth label={label}
                        disabled={busy || !selectedTable} value={mapping[key]} onChange={event => change(key, event.target.value)}>
                        {(selectedTable?.columns ?? []).map(column => <MenuItem key={column.name} value={column.name}>{column.name}</MenuItem>)}
                    </TextField>)}
                    <Stack direction="row" spacing={2}>
                        <TextField fullWidth label="Control label" value={mapping.control} disabled={busy} onChange={event => change('control', event.target.value)} />
                        <TextField fullWidth label="Treatment label" value={mapping.treatment} disabled={busy} onChange={event => change('treatment', event.target.value)} />
                    </Stack>
                    <TextField label="Planned treatment fraction" type="number" value={mapping.expected_treatment_fraction}
                        disabled={busy} inputProps={{ min: 0.05, max: 0.95, step: 0.05 }}
                        helperText="Use the assigned ratio, not the observed share. 0.5 means a planned 50/50 split."
                        onChange={event => change('expected_treatment_fraction', Number(event.target.value))} />
                    <Box>
                        {declarations.map(([key, label]) => <FormControlLabel key={key} sx={{ alignItems: 'flex-start', mb: 1 }}
                            control={<Checkbox checked={mapping[key]} disabled={busy} onChange={event => change(key, event.target.checked)} />}
                            label={<Typography variant="body2" sx={{ pt: 1 }}>{label}</Typography>} />)}
                    </Box>
                    <Button variant="contained" disabled={busy || !canAnalyze} onClick={analyze}>Validate and analyze</Button>
                </Stack>
            </Paper>
            <Paper variant="outlined" sx={panel}>
                <Typography component="h2" variant="h6" gutterBottom>3. Inspect the evidence</Typography>
                {busy && <><LinearProgress aria-label="Experiment operation in progress" /><Typography role="status" sx={{ mt: 2 }}>{run.status === 'analyzing' ? 'Validating the complete table…' : run.status === 'uploading' ? 'Preserving the table in this workspace…' : 'Reading workspace tables…'}</Typography></>}
                {run.status === 'error' && <Alert severity="error">{run.message}</Alert>}
                {run.status === 'result' ? <ReportView report={run.report} /> : !busy && run.status !== 'error' && <>
                    <Typography color="text.secondary" sx={{ mb: 2 }}>Results appear after the full table passes validation. No rows are sampled or silently dropped.</Typography>
                    <Typography variant="body2">Requires 30 or more independent units per arm, finite metrics and a varying pre-period covariate. Serious sample-ratio mismatch blocks inference.</Typography>
                </>}
            </Paper>
        </Box>
    </Stack>;
}

function ReportView({ report }: { report: Report }) {
    function download() {
        const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }));
        const link = document.createElement('a');
        link.href = url; link.download = `trialdesk-${report.selected_input_sha256.slice(0, 12)}.json`;
        link.click(); URL.revokeObjectURL(url);
    }
    return <Stack spacing={2}>
        <Alert severity={report.status === 'ready' ? 'success' : 'warning'}>
            {report.status === 'ready' ? 'Validation passed. Review the estimates and assumptions.' : 'Inference blocked. Resolve the diagnostic before interpreting effects.'}
        </Alert>
        <Typography variant="body2">{report.counts.control.toLocaleString()} control · {report.counts.treatment.toLocaleString()} treatment · allocation p = {format(report.sample_ratio_p_value)}</Typography>
        {report.diagnostics.map(item => <Alert key={item.code} severity={item.severity}>{item.message}</Alert>)}
        {report.status === 'ready' && <>
            <TableContainer>
                <Table size="small" aria-label="Treatment effect estimates">
                    <TableHead><TableRow><TableCell>Estimate</TableCell><TableCell align="right">Effect</TableCell><TableCell align="right">95% interval</TableCell><TableCell align="right">p</TableCell></TableRow></TableHead>
                    <TableBody>{(['ordinary', 'cuped'] as const).map(method => <TableRow key={method}>
                        <TableCell component="th" scope="row">{method === 'ordinary' ? 'Ordinary' : 'CUPED'}</TableCell>
                        <TableCell align="right">{format(report.estimates[method].effect)}</TableCell>
                        <TableCell align="right">[{report.estimates[method].confidence_interval.map(format).join(', ')}]</TableCell>
                        <TableCell align="right">{format(report.estimates[method].p_value)}</TableCell>
                    </TableRow>)}</TableBody>
                </Table>
            </TableContainer>
            <Typography variant="body2">Estimated effect variance change: {format(report.estimated_effect_variance_reduction * 100)}% reduction. Common adjustment coefficient: {format(report.theta)}.</Typography>
            <Typography variant="body2" color="text.secondary">Approximate fixed-horizon intervals. This is statistical evidence under the declared design, not an automatic shipping decision.</Typography>
        </>}
        <Box>
            <Typography variant="subtitle2">Selected-input SHA-256</Typography>
            <Typography component="code" variant="caption" sx={{ overflowWrap: 'anywhere' }}>{report.selected_input_sha256}</Typography>
        </Box>
        <Button variant="outlined" onClick={download}>Export analysis JSON</Button>
        <Box component="details"><Typography component="summary">Assumptions and method limits</Typography>
            <Box component="ul" sx={{ pl: 2 }}>{report.assumptions.map(text => <Typography key={text} component="li" variant="body2" sx={{ my: 1 }}>{text}</Typography>)}</Box>
            <Typography variant="caption">{report.method_version} · Hash covers the selected columns and unit-sorted values, not the complete source file. Keep your input CSV with this export.</Typography>
        </Box>
    </Stack>;
}
