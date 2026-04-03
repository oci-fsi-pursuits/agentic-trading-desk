PYTHON := python3

.PHONY: export-spec test validate conformance serve clean

export-spec:
	$(PYTHON) authoring/export_spec.py

validate: export-spec
	$(PYTHON) -m py_compile app.py authoring/export_spec.py runtime/common/types.py runtime/common/registry.py runtime/common/utils.py runtime/common/scenario_loader.py runtime/common/quant_runner.py runtime/common/store.py runtime/common/engine.py runtime/common/service.py runtime/common/contract_validation.py runtime/common/env_validation.py runtime/common/agent_prompts.py runtime/common/oci_genai.py runtime/wayflow/adapter.py runtime/langgraph/adapter.py tests/conformance/run_conformance.py tests/validate_repo.py tests/validate_runtime_contracts.py tests/validate_deployment_hardening.py tests/validate_agent_prompts.py
	$(PYTHON) tests/validate_repo.py
	$(PYTHON) tests/validate_runtime_contracts.py
	$(PYTHON) tests/validate_deployment_hardening.py
	$(PYTHON) tests/validate_agent_prompts.py

conformance: export-spec
	$(PYTHON) tests/conformance/run_conformance.py

test: validate conformance

serve:
	$(PYTHON) app.py

clean:
	rm -rf __pycache__ authoring/__pycache__ runtime/__pycache__ runtime/common/__pycache__ runtime/wayflow/__pycache__ runtime/langgraph/__pycache__ var
