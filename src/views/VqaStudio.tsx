import React, { useEffect, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { Link } from 'react-router-dom';
import { Alert, Box, Button, CircularProgress, MenuItem, Tab, Tabs, TextField, ThemeProvider, createTheme } from '@mui/material';
import { apiRequest } from '../app/apiClient';
import { fetchWithIdentity } from '../app/utils';
import { DataFormulatorState, dfActions } from '../app/dfSlice';
import { generateUUID } from '../app/identity';
import { palettes } from '../app/tokens';
import './VqaStudio.css';

type Item = { id?: string; question: string; context: string; answer: string; answer_mode: 'mcq' | 'exact' | 'manual';
    answer_label: number | null; choices: string[]; aliases: string[]; split: string; image_sha256?: string; revision?: number; image?: string };
type Version = { id: string; manifest: { split: string; items: Item[] } };
type Result = { id: string; prediction: string; cache_hit: boolean; score: { status: string; correct: boolean | null }; vision_forwards?: number };
type Run = { id: string; version: string; status: string; model: string; scorer: string; error: string | null; rankable: boolean;
    metrics: { total: number; completed: number; scored: number; correct: number; manual: number; accuracy: number | null }; results: Result[]; cache_hits: number };
type State = { items: Item[]; versions: Version[]; runs: Run[] };
const empty: Item = { question: '', context: '', answer: '', answer_mode: 'exact', answer_label: null, choices: [], aliases: [], split: 'validation' };
const theme = createTheme({ palette: { mode: 'dark', background: { default: '#0b0f19', paper: '#0c0f17' }, primary: { main: '#c6cbd5' },
    derived: palettes.material.derived, custom: palettes.material.custom },
    typography: { fontFamily: 'Roboto, system-ui, sans-serif', fontSize: 13 }, shape: { borderRadius: 8 } });

function Preview({ hash, workspace, large = false }: { hash?: string; workspace: string; large?: boolean }) {
    const [url, setUrl] = useState('');
    useEffect(() => {
        let object = ''; let active = true;
        if (hash) fetchWithIdentity(`/api/vqa/images/${hash}`, { headers: { 'X-Workspace-Id': workspace } })
            .then(response => { if (!response.ok) throw new Error('Image unavailable'); return response.blob(); })
            .then(blob => { object = URL.createObjectURL(blob); if (active) setUrl(object); else URL.revokeObjectURL(object); })
            .catch(() => { if (active) setUrl(''); });
        return () => { active = false; if (object) URL.revokeObjectURL(object); };
    }, [hash, workspace]);
    return url ? <img className={large ? 'vqa-image-large' : 'vqa-thumbnail'} src={url} alt={large ? 'Selected annotated image' : 'Question image'} /> : <span>—</span>;
}

export function VqaStudio() {
    const workspace = useSelector((state: DataFormulatorState) => state.activeWorkspace);
    const dispatch = useDispatch();
    return <ThemeProvider theme={theme}><Box className="vqa-root" component="main">
        {workspace ? <Studio key={workspace.id} workspace={workspace.id} readOnly={!!workspace.readOnly} /> : <>
            <header className="vqa-header"><strong>Visual question answering evaluation</strong><Link to="/app">Workspaces</Link></header>
            <section className="vqa-welcome"><h1>Start a dataset workspace</h1><p>Annotate your images, freeze a reproducible split and evaluate a pinned local vision model.</p>
                <Button variant="outlined" onClick={() => dispatch(dfActions.resetForNewWorkspace({ id: generateUUID(), displayName: 'VQA dataset' }))}>Create VQA workspace</Button>
                <p>Images stay in your workspace. No model download, API key or external judge is used here.</p></section></>}
    </Box></ThemeProvider>;
}

function Studio({ workspace, readOnly }: { workspace: string; readOnly: boolean }) {
    const [state, setState] = useState<State>({ items: [], versions: [], runs: [] });
    const [draft, setDraft] = useState<Item>({ ...empty });
    const [tab, setTab] = useState(0);
    const [split, setSplit] = useState('validation');
    const [search, setSearch] = useState('');
    const [version, setVersion] = useState('');
    const [runId, setRunId] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [notice, setNotice] = useState('');
    const headers = { 'X-Workspace-Id': workspace, 'Content-Type': 'application/json' };
    const active = state.runs.find(run => run.status === 'running');
    const selectedRun = state.runs.find(run => run.id === runId) ?? state.runs[0];
    const rows = state.items.filter(item => item.split === split && `${item.question} ${item.answer}`.toLowerCase().includes(search.toLowerCase()));
    async function refresh() {
        const { data } = await apiRequest<State>('/api/vqa/state', { headers }); setState(data);
    }
    useEffect(() => {
        const controller = new AbortController();
        apiRequest<{ sessions: { id: string }[] }>('/api/sessions/list', { headers, signal: controller.signal })
            .then(({ data }) => {
                if (!data.sessions.some(session => session.id === workspace) && !readOnly)
                    return apiRequest('/api/sessions/create', { method: 'POST', headers, body: JSON.stringify({ id: workspace }), signal: controller.signal });
            })
            .then(() => apiRequest<State>('/api/vqa/state', { headers, signal: controller.signal }))
            .then(({ data }) => setState(data)).catch(e => { if (!controller.signal.aborted) setError(String(e)); });
        return () => controller.abort();
    }, [workspace, readOnly]);
    useEffect(() => {
        if (!active) return;
        const timer = window.setInterval(() => { refresh().catch(e => setError(String(e))); }, 1200);
        return () => window.clearInterval(timer);
    }, [active?.id]);
    async function action(work: () => Promise<void>) {
        setBusy(true); setError(''); setNotice('');
        try { await work(); await refresh(); } catch (e) { setError(String(e)); } finally { setBusy(false); }
    }
    async function upload(file?: File) {
        if (!file) return;
        if (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 2_000_000) { setError('Choose a PNG or JPEG image up to 2 MB.'); return; }
        const bytes = new Uint8Array(await file.arrayBuffer()); let binary = '';
        bytes.forEach(byte => { binary += String.fromCharCode(byte); });
        setDraft(previous => ({ ...previous, image: btoa(binary) }));
    }
    async function save() {
        const { data } = await apiRequest<Item>('/api/vqa/items', { method: 'POST', headers, body: JSON.stringify(draft) });
        setDraft(data); setNotice('Annotation saved. Existing frozen versions remain unchanged.');
    }
    async function freeze() {
        const { data } = await apiRequest<Version>('/api/vqa/versions', { method: 'POST', headers, body: JSON.stringify({ split }) });
        setVersion(data.id); setNotice(`Frozen version ${data.id.slice(0, 12)}. Select Experiments to evaluate.`);
    }
    async function evaluate() {
        const { data } = await apiRequest<Run>('/api/vqa/runs', { method: 'POST', headers, body: JSON.stringify({ version }) });
        setRunId(data.id); setTab(1);
    }
    async function exportRun(run: Run) {
        const { data } = await apiRequest(`/api/vqa/runs/${run.id}/export`, { headers });
        const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
        const anchor = document.createElement('a'); anchor.href = url; anchor.download = `vqa-${run.id}.json`; anchor.click(); URL.revokeObjectURL(url);
    }
    const versions = state.versions.filter(v => v.manifest.split === split);
    const comparable = state.runs.filter(run => run.version === version && run.scorer === 'mode-exact-v2');
    return <>
        <header className="vqa-header"><strong>Datasets: <span>visual-question-answering</span></strong>
            <Tabs value={tab} onChange={(_, value: number) => setTab(value)} aria-label="VQA workspace views">
                <Tab label="Dataset" /><Tab label="Experiments" /><Tab label="Leaderboard" /></Tabs><Link to="/app">Workspaces</Link></header>
        {error && <Alert severity="error" onClose={() => setError('')}>{error}</Alert>}
        {notice && <Alert severity="success" onClose={() => setNotice('')}>{notice}</Alert>}
        <div className="vqa-columns"><section className="vqa-left" aria-label="Dataset and evaluations">
            <div className="vqa-split"><TextField select size="small" label="Split" value={split} onChange={e => { setSplit(e.target.value); setVersion(''); }}>
                {['train', 'validation', 'test'].map(x => <MenuItem key={x} value={x}>{x}</MenuItem>)}</TextField><span>{state.items.filter(x => x.split === split).length} rows</span>
                <Button disabled={busy || readOnly || !rows.length} onClick={() => action(freeze)}>Freeze version</Button></div>
            {tab === 0 ? <div className="vqa-table-panel"><TextField className="vqa-search" fullWidth variant="standard" placeholder="Search this dataset" value={search} onChange={e => setSearch(e.target.value)} slotProps={{ input: { disableUnderline: true }, htmlInput: { 'aria-label': 'Search this dataset' } }} />
                <div className="vqa-table-scroll"><table className="vqa-table"><thead><tr><th>answer<span>string · annotation</span><small>{rows.length} values</small></th><th>question<span>string · context</span><small>{rows.filter(x => x.answer_mode === 'mcq').length} multiple choice</small></th><th>image<span>PNG · local bytes</span><small>SHA-256</small></th></tr></thead>
                    <tbody>{rows.map(item => <tr key={item.id} className={draft.id === item.id ? 'selected' : ''}><td><button className="vqa-row-button" onClick={() => setDraft(item)}>{item.answer || 'Manual review'}</button></td><td><button className="vqa-row-button" onClick={() => setDraft(item)}>{item.question}</button></td><td><Preview hash={item.image_sha256} workspace={workspace} /></td></tr>)}</tbody></table>
                    {!rows.length && <div className="vqa-empty">{search ? 'No annotations match this search.' : 'No annotations in this split. Add an image and question in the inspector.'}</div>}</div>
                <footer className="vqa-table-footer">{rows.length} rows · {state.versions.length} immutable versions <Button onClick={() => setDraft({ ...empty, split })}>New annotation</Button></footer></div> : <div className="vqa-table-panel vqa-runs">
                <h2>{tab === 1 ? 'Evaluation runs' : 'Comparable runs'}</h2><p>{tab === 1 ? 'Every run retains its frozen input, model revision, scoring policy and raw predictions.' : 'Choose one immutable version to compare. Only completed, fully scored runs are ranked.'}</p>
                <TextField select size="small" fullWidth label="Dataset version" value={version} onChange={e => setVersion(e.target.value)}>{versions.map(v => <MenuItem key={v.id} value={v.id}>{v.id.slice(0, 12)} · {v.manifest.items.length} items · {v.manifest.split}</MenuItem>)}</TextField>
                <table className="vqa-table"><thead><tr><th>Run / status</th><th>Score / coverage</th><th>Inspect</th></tr></thead><tbody>{(tab === 1 ? state.runs : [...comparable].sort((a, b) => Number(b.rankable) - Number(a.rankable) || (b.metrics.accuracy ?? -1) - (a.metrics.accuracy ?? -1))).map(run => <tr key={run.id}><td>{run.id.slice(0, 8)}<span className="vqa-status">{run.status}{run.rankable ? ' · ranked' : ' · unranked'}</span></td><td>{run.metrics.correct}/{run.metrics.scored} scored<br />{run.metrics.completed}/{run.metrics.total} completed</td><td><Button onClick={() => { setRunId(run.id); setTab(1); }}>Inspect</Button><Button onClick={() => action(() => exportRun(run))}>Export JSON</Button></td></tr>)}</tbody></table>
                {!(tab === 1 ? state.runs : comparable).length && <p>No runs for this selection.</p>}
                <p>Synthetic data checks mechanisms only. Accuracy here is not a generalization estimate. Manual answers are unscored.</p></div>}
        </section><aside className="vqa-inspector" aria-label={tab === 0 ? 'Annotation inspector' : 'Run inspector'}>
            {tab === 0 ? <>
                <div className="vqa-inspector-preview">{draft.image ? <img className="vqa-image-large" src={`data:image/png;base64,${draft.image}`} alt="New annotation preview" /> : draft.image_sha256 ? <Preview hash={draft.image_sha256} workspace={workspace} large /> : <p>Upload a PNG or JPEG image<br /><small>Up to 2 MB · 2048px per side · two million pixels</small></p>}</div>
                <Button component="label" fullWidth variant="outlined" disabled={readOnly}>Choose image<input hidden type="file" accept="image/png,image/jpeg" aria-label="Choose annotation image" onChange={e => { upload(e.target.files?.[0]).catch(err => setError(String(err))); e.target.value = ''; }} /></Button>
                <TextField label="Question" value={draft.question} multiline maxRows={3} onChange={e => setDraft({ ...draft, question: e.target.value })} size="small" fullWidth />
                <TextField label="Context (optional)" value={draft.context} multiline maxRows={3} onChange={e => setDraft({ ...draft, context: e.target.value })} size="small" fullWidth />
                <div className="vqa-field-pair"><TextField select label="Answer mode" value={draft.answer_mode} onChange={e => setDraft({ ...draft, answer_mode: e.target.value as Item['answer_mode'], answer_label: null })} size="small"><MenuItem value="exact">Exact / aliases</MenuItem><MenuItem value="mcq">Multiple choice</MenuItem><MenuItem value="manual">Manual review</MenuItem></TextField>
                    <TextField select label="Annotation split" value={draft.split} onChange={e => setDraft({ ...draft, split: e.target.value })} size="small">{['train', 'validation', 'test'].map(x => <MenuItem key={x} value={x}>{x}</MenuItem>)}</TextField></div>
                {draft.answer_mode === 'mcq' ? <><TextField label="Choices (one per line)" value={draft.choices.join('\n')} multiline onChange={e => setDraft({ ...draft, choices: e.target.value.split('\n'), answer_label: null, answer: '' })} size="small" fullWidth />
                    <TextField select label="Correct choice" value={draft.answer_label ?? ''} onChange={e => { const label = Number(e.target.value); setDraft({ ...draft, answer_label: label, answer: draft.choices[label] }); }} size="small" fullWidth>{draft.choices.map((x, i) => <MenuItem key={i} value={i}>{String.fromCharCode(65 + i)}. {x}</MenuItem>)}</TextField></> : <>
                    <TextField label={draft.answer_mode === 'manual' ? 'Reviewer reference (optional)' : 'Gold answer'} value={draft.answer} onChange={e => setDraft({ ...draft, answer: e.target.value })} size="small" fullWidth />
                    {draft.answer_mode === 'exact' && <TextField label="Accepted aliases (one per line)" value={draft.aliases.join('\n')} multiline onChange={e => setDraft({ ...draft, aliases: e.target.value ? e.target.value.split('\n') : [] })} size="small" fullWidth />}</>}
                <Button className="vqa-primary" variant="outlined" fullWidth disabled={busy || readOnly || !draft.question || (!draft.image && !draft.image_sha256)} onClick={() => action(save)}>{busy ? 'Saving…' : draft.id ? 'Save annotation revision' : 'Save annotation'}</Button>
                <small>Gold answers are excluded from model prompts and answer cache keys. Freeze a new version after editing.</small>
            </> : <>
                <div className="vqa-run-summary"><h2>{selectedRun ? `Run ${selectedRun.id.slice(0, 8)}` : 'Local evaluation'}</h2><p>HuggingFaceTB/SmolVLM-256M-Instruct</p><p>CPU · pinned revision · 32 tokens · offline</p>{selectedRun && <><p>Status: <strong>{selectedRun.status}</strong></p><p>{selectedRun.metrics.completed}/{selectedRun.metrics.total} completed · {selectedRun.cache_hits} answer cache hits</p><p>{selectedRun.metrics.manual} manual / unscored</p></>}</div>
                <h2>Subsets and versions</h2><TextField select label="Frozen version" size="small" fullWidth value={version} onChange={e => setVersion(e.target.value)}>{versions.map(v => <MenuItem key={v.id} value={v.id}>{v.id.slice(0, 12)} · {v.manifest.items.length} items</MenuItem>)}</TextField>
                <Button className="vqa-primary" variant="outlined" fullWidth disabled={busy || !!active || readOnly || !version} onClick={() => action(evaluate)}>{active ? <><CircularProgress size={16} /> Evaluation running</> : 'Run evaluation'}</Button>
                {active && <Button fullWidth onClick={() => action(async () => { await apiRequest(`/api/vqa/runs/${active.id}/cancel`, { method: 'POST', headers, body: '{}' }); })}>Cancel active run</Button>}
                {selectedRun?.error && <Alert severity="error">{selectedRun.error}</Alert>}
                {selectedRun && <><Button onClick={() => action(() => exportRun(selectedRun))}>Export reproducible artifact</Button><div className="vqa-predictions">{selectedRun.results.map(result => <article key={result.id}><strong>{state.versions.find(v => v.id === selectedRun.version)?.manifest.items.find(i => i.id === result.id)?.question}</strong><p>{result.prediction}</p><small>{result.score.status} · {result.score.correct === null ? 'unscored' : result.score.correct ? 'correct' : 'incorrect'} · {result.cache_hit ? 'cached' : 'generated'}</small></article>)}</div></>}
                <small>MCQ uses letter/choice matching. Exact mode uses normalized answers and explicit aliases. Freeform review requires a human; no automatic judge is fabricated.</small>
            </>}
        </aside></div>
    </>;
}
