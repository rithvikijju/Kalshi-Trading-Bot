"""Builds kalshi_arb_strategy_live.ipynb from strategy_cells/*.{md,py}.
Each file becomes one cell (.md -> markdown, .py -> code), in filename order.
Cells with names like 09_*.py become additional code cells.
"""
import json
from pathlib import Path

cells = []
src_dir = Path('strategy_cells')

for path in sorted(src_dir.iterdir()):
    if path.suffix not in ('.md', '.py'):
        continue
    text = path.read_text()
    lines = text.splitlines(keepends=True)
    if path.suffix == '.md':
        cells.append({
            'cell_type': 'markdown', 'metadata': {}, 'source': lines,
        })
    else:
        cells.append({
            'cell_type': 'code', 'metadata': {},
            'execution_count': None, 'outputs': [], 'source': lines,
        })

# Add the launch/control cells at the end
extras = [
    ('code', '# Start in paper mode (default)\nstart_bot()\n'),
    ('code', '# Live-updating portfolio dashboard with mark-to-market PnL.\n# Refreshes every 2s. Press ■ (stop) in Jupyter to halt.\nlive_status()\n'),
    ('code', '# One-shot snapshot (non-blocking)\nstatus()\n'),
    ('code', '# Verbose explainer — every candidate the strategy sees + why\nthink()\n'),
    ('code', 'diagnostics()\n'),
    ('code', 'tick_stats()\n'),
    ('code', '# After an overnight run: lock in a backtestable snapshot of the ticks DB.\n# Then in shell:  python backtest_outputs/merge_overnight_capture.py <path>\n# snapshot_ticks_db("night1")\n'),
    ('code', '# Paper account snapshot (cash, locked, realized PnL, win rate, drawdown)\npaper_stats()\n'),
    ('code', '# To wipe paper account and restart with $100:\n# paper_reset()\n'),
    ('code', 'trade_history()\n'),
    ('code', 'stop_bot()\n'),
    ('code', '# Uncomment to go live (after verifying paper mode):\n# enable_live()\n# start_bot()\n'),
    ('code', '# Emergency stop:\n# kill_switch()\n'),
]
for ctype, src in extras:
    cells.append({
        'cell_type': ctype, 'metadata': {},
        'execution_count': None, 'outputs': [],
        'source': src.splitlines(keepends=True),
    })

nb = {
    'cells': cells,
    'metadata': {
        'kernelspec': {'display_name': 'Python 3', 'language': 'python', 'name': 'python3'},
        'language_info': {'name': 'python', 'version': '3.11'},
    },
    'nbformat': 4,
    'nbformat_minor': 5,
}

out = Path('kalshi_arb_strategy_live.ipynb')
with open(out, 'w') as f:
    json.dump(nb, f, indent=1)
print(f'wrote {out}  ({out.stat().st_size:,} bytes, {len(cells)} cells)')
