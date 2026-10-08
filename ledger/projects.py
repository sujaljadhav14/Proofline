import re
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.orm import Session
from ledger.db import Project
from ledger.util import utcnow


def normalize_project_string(s: str) -> str:
    """Normalize project name or alias: lowercase and alphanumeric only."""
    return re.sub(r'[^a-z0-9]', '', s.lower())


def resolve_project(name_or_alias: str, projects: List[Project]) -> Optional[Project]:
    """Match project against name and aliases ignoring case, spaces, and punctuation."""
    norm = normalize_project_string(name_or_alias)
    if not norm:
        return None

    for p in projects:
        if normalize_project_string(p.name) == norm:
            return p
        for alias in (p.aliases or []):
            if normalize_project_string(alias) == norm:
                return p
    return None


def parse_project_def(line: str) -> tuple[str, list[str]]:
    """Parse 'Name = alias1, alias2' into ('Name', ['alias1', 'alias2'])."""
    line = line.strip()
    if "=" in line:
        parts = line.split("=", 1)
        name = parts[0].strip()
        aliases = [a.strip() for a in parts[1].split(",") if a.strip()]
        return name, aliases
    return line, []


def add_project(session: Session, owner_id: int, name: str, aliases: list[str]) -> Project:
    """Add or update a project for an owner."""
    stmt = select(Project).where(Project.owner_id == owner_id, Project.name == name)
    existing = session.scalar(stmt)
    if existing:
        # Merge aliases
        current_aliases = set(existing.aliases or [])
        current_aliases.update(aliases)
        existing.aliases = list(current_aliases)
        session.commit()
        return existing

    proj = Project(
        owner_id=owner_id,
        name=name,
        aliases=aliases,
        repo_paths=[],
        created_at=utcnow(),
    )
    session.add(proj)
    session.commit()
    return proj


def list_projects(session: Session, owner_id: int) -> List[Project]:
    """Return all projects for an owner ordered by name."""
    stmt = select(Project).where(Project.owner_id == owner_id).order_by(Project.name.asc())
    return list(session.scalars(stmt).all())


def seed_projects_from_env(session: Session, owner_id: int, seed_str: str) -> List[Project]:
    """Seed projects from SEED_PROJECTS string e.g. 'WebApp=web,site;Mobile=app'."""
    if not seed_str:
        return []
    created = []
    defs = [d.strip() for d in seed_str.split(";") if d.strip()]
    for d in defs:
        name, aliases = parse_project_def(d)
        if name:
            proj = add_project(session, owner_id, name, aliases)
            created.append(proj)
    return created
