"""Execute the same frontend business adapters; no arbitrary Python or shell."""
import json
import os
from pathlib import Path
import sys

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))


def execute(request):
    from tool_runtime import Catalog, Executor, PERSONAL_TOOLS, ToolError
    catalog = Catalog()
    name = request.get('name')
    if name not in catalog.tools:
        raise ToolError('unknown_tool', 'The tool is not in the actual frontend registry.')
    context=request.get('personal_context') or {}
    authorized=(context.get('personal_authorized')is True and context.get('external_results_consented')is True
        and isinstance(context.get('scope'),str) and bool(context['scope']))
    if name in PERSONAL_TOOLS and not authorized:
        # Do not turn the backend's external API credential into permission to
        # read somebody's personal records. The browser grants this separately.
        raise ToolError('authorization_required', 'Personal tools require the existing browser-scoped authorization and connected private store; no personal data was read or changed.')
    if name in PERSONAL_TOOLS:
        from personal_harness import patch_error
        invalid=patch_error(name,request.get('arguments'))
        if invalid:raise ToolError(*invalid)
    value = Executor(catalog,personal_scope=context.get('scope')if authorized else None).execute(
        name, request.get('arguments'), personal_authorized=authorized)
    # Graph points are UI-only. Exact business observations and honest source
    # labels match the public frontend adapter, including fixture notices.
    provenance = value.get('provenance', {})
    value['provenance'] = {k: v for k, v in provenance.items() if k not in ('chart', 'charts', 'series', 'chart_series')}
    value['canonical_tool'] = name
    value['selection_origin'] = 'backend_model'
    return value


def main():
    try:
        raw = sys.stdin.read(32001)
        if len(raw) > 32000:
            raise ValueError('Bounded tool request required')
        value = execute(json.loads(raw))
    except Exception as error:
        # Never reflect transport diagnostics, headers or personal data.
        value = {'error': {'code': getattr(error, 'code', 'adapter_failure'),
            'message': str(error) if getattr(error, 'code', '') in ('authorization_required', 'unknown_tool') else
            'Frontend adapter failed; do not claim a successful observation.'}}
    print(json.dumps(value, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
