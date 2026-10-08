# Work Ledger: agent instructions

The full build brief is `BUILD_BRIEF.md` in this folder. **Read it completely, every section, before writing any code.** It is long. Do not skim it.

Rules that always apply:
- Build the phases in BUILD_BRIEF.md section 11 in order. Build Phase 1 only unless the user says otherwise, then stop and report.
- Build nothing that is not in the brief. Ask before adding anything.
- Tests must never call real Telegram, LLM, Notion or GitHub services. Use fakes.
- Never auto-close a task from a guess. The user is the authority on status.
- Never commit secrets. Keep .env, *.db and .venv out of git.
- Telegram messages are plain text only. No Markdown or HTML parse modes.
- No company-specific or person-specific names anywhere. Use generic examples.
- Check the current Telegram, Groq, Ollama and Notion docs before coding against them.
- Do not deploy or create accounts. Explain manual steps in the README.
