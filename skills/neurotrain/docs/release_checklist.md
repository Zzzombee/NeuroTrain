# Release Checklist

For `v0.2.0-managed`:

1. Confirm `config_template.yaml` defaults to `plotting.engine: matplotlib`.
2. Confirm `origin.enabled: false`.
3. Confirm no default dependency on `originpro`, pywinauto, pywin32, COM, or OriginPro.
4. Run:

```powershell
python -m py_compile run_pipeline.py
python -m compileall scripts utils
python -m unittest discover -s tests
```

5. Run a minimal smoke test.
6. Record `99_project_management/test_report.md`.
7. Record `99_project_management/live_test_report.md` if a real local project is used.
8. Update `VERSION` and `CHANGELOG.md`.
9. Commit.
10. Tag only after tests and smoke test pass:

```powershell
git tag v0.2.0-managed
```

