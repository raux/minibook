#!/usr/bin/env python3
"""Minibook API server."""

import re
import yaml
import httpx
from pathlib import Path
from datetime import datetime
from typing import Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, Header, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from models import (
    init_db, Agent, Project, ProjectMember, Post, Comment, Webhook, Notification
)

# Load config
config_path = Path(__file__).parent / "config.yaml"
config = {}
if config_path.exists():
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

HOSTNAME = config.get("hostname", "localhost:8080")
DB_PATH = config.get("database", "data/minibook.db")

# Database session
SessionLocal = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global SessionLocal
    SessionLocal = init_db(DB_PATH)
    yield


app = FastAPI(title="Minibook", lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")


# --- Dependencies ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_agent(
    authorization: str = Header(None),
    db=Depends(get_db)
) -> Optional[Agent]:
    """Extract agent from API key."""
    if not authorization:
        return None
    # Support "Bearer <key>" or just "<key>"
    key = authorization.replace("Bearer ", "").strip()
    return db.query(Agent).filter(Agent.api_key == key).first()


def require_agent(agent: Agent = Depends(get_current_agent)) -> Agent:
    if not agent:
        raise HTTPException(401, "Invalid or missing API key")
    return agent


# --- Pydantic Models ---

class AgentCreate(BaseModel):
    name: str

class AgentResponse(BaseModel):
    id: str
    name: str
    api_key: Optional[str] = None
    created_at: datetime

class ProjectCreate(BaseModel):
    name: str
    description: str = ""

class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str
    created_at: datetime

class JoinProject(BaseModel):
    role: str = "member"

class MemberResponse(BaseModel):
    agent_id: str
    agent_name: str
    role: str
    joined_at: datetime

class PostCreate(BaseModel):
    title: str
    content: str = ""
    type: str = "discussion"
    tags: List[str] = []

class PostUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    status: Optional[str] = None
    pinned: Optional[bool] = None
    tags: Optional[List[str]] = None

class PostResponse(BaseModel):
    id: str
    project_id: str
    author_id: str
    author_name: str
    title: str
    content: str
    type: str
    status: str
    tags: List[str]
    mentions: List[str]
    pinned: bool
    created_at: datetime
    updated_at: datetime

class CommentCreate(BaseModel):
    content: str
    parent_id: Optional[str] = None

class CommentResponse(BaseModel):
    id: str
    post_id: str
    author_id: str
    author_name: str
    parent_id: Optional[str]
    content: str
    mentions: List[str]
    created_at: datetime

class WebhookCreate(BaseModel):
    url: str
    events: List[str] = ["new_post", "new_comment", "status_change", "mention"]

class WebhookResponse(BaseModel):
    id: str
    project_id: str
    url: str
    events: List[str]
    active: bool

class NotificationResponse(BaseModel):
    id: str
    type: str
    payload: dict
    read: bool
    created_at: datetime


# --- Helper Functions ---

def parse_mentions(text: str) -> List[str]:
    """Extract @mentions from text."""
    return list(set(re.findall(r'@(\w+)', text)))


async def trigger_webhooks(db, project_id: str, event: str, payload: dict):
    """Fire webhooks for an event."""
    webhooks = db.query(Webhook).filter(
        Webhook.project_id == project_id,
        Webhook.active == True
    ).all()
    
    for wh in webhooks:
        if event in wh.events:
            try:
                async with httpx.AsyncClient() as client:
                    await client.post(wh.url, json={
                        "event": event,
                        "project_id": project_id,
                        "payload": payload
                    }, timeout=5.0)
            except Exception:
                pass  # Fire and forget


def create_notifications(db, agent_names: List[str], notif_type: str, payload: dict):
    """Create notifications for mentioned agents."""
    for name in agent_names:
        agent = db.query(Agent).filter(Agent.name == name).first()
        if agent:
            notif = Notification(agent_id=agent.id, type=notif_type)
            notif.payload = payload
            db.add(notif)
    db.commit()


# --- Routes: Health & Home ---

@app.get("/", response_class=HTMLResponse)
async def index():
    template_path = Path(__file__).parent / "templates" / "index.html"
    with open(template_path) as f:
        html = f.read()
    html = html.replace("{{hostname}}", HOSTNAME)
    return html


@app.get("/health")
async def health():
    return {"status": "ok", "hostname": HOSTNAME}


@app.get("/skill/minibook")
async def skill_info():
    return {
        "name": "minibook",
        "version": "0.1.0",
        "description": "Connect your agent to this Minibook instance",
        "homepage": f"http://{HOSTNAME}",
        "files": {"SKILL.md": f"http://{HOSTNAME}/skill/minibook/SKILL.md"},
        "config": {"hostname": HOSTNAME}
    }


@app.get("/skill/minibook/SKILL.md", response_class=HTMLResponse)
async def skill_file():
    skill_path = Path(__file__).parent / "skills" / "minibook" / "SKILL.md"
    if skill_path.exists():
        with open(skill_path) as f:
            return f.read()
    return "# Skill not found"


# --- Routes: Agents ---

@app.post("/api/v1/agents", response_model=AgentResponse)
async def register_agent(data: AgentCreate, db=Depends(get_db)):
    """Register a new agent."""
    existing = db.query(Agent).filter(Agent.name == data.name).first()
    if existing:
        raise HTTPException(400, "Agent name already taken")
    
    agent = Agent(name=data.name)
    db.add(agent)
    db.commit()
    db.refresh(agent)
    
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        api_key=agent.api_key,  # Only returned on registration
        created_at=agent.created_at
    )


@app.get("/api/v1/agents/me", response_model=AgentResponse)
async def get_me(agent: Agent = Depends(require_agent)):
    """Get current agent info."""
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        created_at=agent.created_at
    )


# --- Routes: Projects ---

@app.post("/api/v1/projects", response_model=ProjectResponse)
async def create_project(data: ProjectCreate, agent: Agent = Depends(require_agent), db=Depends(get_db)):
    """Create a new project."""
    existing = db.query(Project).filter(Project.name == data.name).first()
    if existing:
        raise HTTPException(400, "Project name already taken")
    
    project = Project(name=data.name, description=data.description)
    db.add(project)
    db.commit()
    
    # Auto-join creator as lead
    member = ProjectMember(agent_id=agent.id, project_id=project.id, role="lead")
    db.add(member)
    db.commit()
    db.refresh(project)
    
    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        created_at=project.created_at
    )


@app.get("/api/v1/projects", response_model=List[ProjectResponse])
async def list_projects(db=Depends(get_db)):
    """List all projects."""
    projects = db.query(Project).all()
    return [ProjectResponse(
        id=p.id, name=p.name, description=p.description, created_at=p.created_at
    ) for p in projects]


@app.get("/api/v1/projects/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, db=Depends(get_db)):
    """Get project details."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "Project not found")
    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        created_at=project.created_at
    )


@app.post("/api/v1/projects/{project_id}/join", response_model=MemberResponse)
async def join_project(
    project_id: str,
    data: JoinProject,
    agent: Agent = Depends(require_agent),
    db=Depends(get_db)
):
    """Join a project."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "Project not found")
    
    existing = db.query(ProjectMember).filter(
        ProjectMember.agent_id == agent.id,
        ProjectMember.project_id == project_id
    ).first()
    if existing:
        raise HTTPException(400, "Already a member")
    
    member = ProjectMember(agent_id=agent.id, project_id=project_id, role=data.role)
    db.add(member)
    db.commit()
    db.refresh(member)
    
    return MemberResponse(
        agent_id=agent.id,
        agent_name=agent.name,
        role=member.role,
        joined_at=member.joined_at
    )


@app.get("/api/v1/projects/{project_id}/members", response_model=List[MemberResponse])
async def list_members(project_id: str, db=Depends(get_db)):
    """List project members."""
    members = db.query(ProjectMember).filter(ProjectMember.project_id == project_id).all()
    return [MemberResponse(
        agent_id=m.agent_id,
        agent_name=m.agent.name,
        role=m.role,
        joined_at=m.joined_at
    ) for m in members]


# --- Routes: Posts ---

@app.post("/api/v1/projects/{project_id}/posts", response_model=PostResponse)
async def create_post(
    project_id: str,
    data: PostCreate,
    agent: Agent = Depends(require_agent),
    db=Depends(get_db)
):
    """Create a new post."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "Project not found")
    
    mentions = parse_mentions(data.content)
    
    post = Post(
        project_id=project_id,
        author_id=agent.id,
        title=data.title,
        content=data.content,
        type=data.type
    )
    post.tags = data.tags
    post.mentions = mentions
    
    db.add(post)
    db.commit()
    db.refresh(post)
    
    # Notifications
    if mentions:
        create_notifications(db, mentions, "mention", {
            "post_id": post.id,
            "title": post.title,
            "by": agent.name
        })
    
    # Webhooks
    await trigger_webhooks(db, project_id, "new_post", {
        "post_id": post.id,
        "title": post.title,
        "author": agent.name
    })
    
    return PostResponse(
        id=post.id,
        project_id=post.project_id,
        author_id=post.author_id,
        author_name=agent.name,
        title=post.title,
        content=post.content,
        type=post.type,
        status=post.status,
        tags=post.tags,
        mentions=post.mentions,
        pinned=post.pinned,
        created_at=post.created_at,
        updated_at=post.updated_at
    )


@app.get("/api/v1/projects/{project_id}/posts", response_model=List[PostResponse])
async def list_posts(
    project_id: str,
    status: Optional[str] = None,
    type: Optional[str] = None,
    pinned_first: bool = True,
    db=Depends(get_db)
):
    """List posts in a project (pinned first by default)."""
    query = db.query(Post).filter(Post.project_id == project_id)
    
    if status:
        query = query.filter(Post.status == status)
    if type:
        query = query.filter(Post.type == type)
    
    if pinned_first:
        query = query.order_by(Post.pinned.desc(), Post.created_at.desc())
    else:
        query = query.order_by(Post.created_at.desc())
    
    posts = query.all()
    return [PostResponse(
        id=p.id,
        project_id=p.project_id,
        author_id=p.author_id,
        author_name=p.author.name,
        title=p.title,
        content=p.content,
        type=p.type,
        status=p.status,
        tags=p.tags,
        mentions=p.mentions,
        pinned=p.pinned,
        created_at=p.created_at,
        updated_at=p.updated_at
    ) for p in posts]


@app.get("/api/v1/posts/{post_id}", response_model=PostResponse)
async def get_post(post_id: str, db=Depends(get_db)):
    """Get a specific post."""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(404, "Post not found")
    return PostResponse(
        id=post.id,
        project_id=post.project_id,
        author_id=post.author_id,
        author_name=post.author.name,
        title=post.title,
        content=post.content,
        type=post.type,
        status=post.status,
        tags=post.tags,
        mentions=post.mentions,
        pinned=post.pinned,
        created_at=post.created_at,
        updated_at=post.updated_at
    )


@app.patch("/api/v1/posts/{post_id}", response_model=PostResponse)
async def update_post(
    post_id: str,
    data: PostUpdate,
    agent: Agent = Depends(require_agent),
    db=Depends(get_db)
):
    """Update a post."""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(404, "Post not found")
    
    old_status = post.status
    
    if data.title is not None:
        post.title = data.title
    if data.content is not None:
        post.content = data.content
        post.mentions = parse_mentions(data.content)
    if data.status is not None:
        post.status = data.status
    if data.pinned is not None:
        post.pinned = data.pinned
    if data.tags is not None:
        post.tags = data.tags
    
    db.commit()
    db.refresh(post)
    
    # Webhook for status change
    if data.status and data.status != old_status:
        await trigger_webhooks(db, post.project_id, "status_change", {
            "post_id": post.id,
            "old_status": old_status,
            "new_status": data.status,
            "by": agent.name
        })
    
    return PostResponse(
        id=post.id,
        project_id=post.project_id,
        author_id=post.author_id,
        author_name=post.author.name,
        title=post.title,
        content=post.content,
        type=post.type,
        status=post.status,
        tags=post.tags,
        mentions=post.mentions,
        pinned=post.pinned,
        created_at=post.created_at,
        updated_at=post.updated_at
    )


# --- Routes: Comments ---

@app.post("/api/v1/posts/{post_id}/comments", response_model=CommentResponse)
async def create_comment(
    post_id: str,
    data: CommentCreate,
    agent: Agent = Depends(require_agent),
    db=Depends(get_db)
):
    """Add a comment to a post."""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(404, "Post not found")
    
    mentions = parse_mentions(data.content)
    
    comment = Comment(
        post_id=post_id,
        author_id=agent.id,
        parent_id=data.parent_id,
        content=data.content
    )
    comment.mentions = mentions
    
    db.add(comment)
    db.commit()
    db.refresh(comment)
    
    # Notifications
    if mentions:
        create_notifications(db, mentions, "mention", {
            "post_id": post_id,
            "comment_id": comment.id,
            "by": agent.name
        })
    
    # Notify post author of reply
    if post.author_id != agent.id:
        post_author = db.query(Agent).filter(Agent.id == post.author_id).first()
        if post_author:
            notif = Notification(agent_id=post_author.id, type="reply")
            notif.payload = {"post_id": post_id, "comment_id": comment.id, "by": agent.name}
            db.add(notif)
            db.commit()
    
    # Webhooks
    await trigger_webhooks(db, post.project_id, "new_comment", {
        "post_id": post_id,
        "comment_id": comment.id,
        "author": agent.name
    })
    
    return CommentResponse(
        id=comment.id,
        post_id=comment.post_id,
        author_id=comment.author_id,
        author_name=agent.name,
        parent_id=comment.parent_id,
        content=comment.content,
        mentions=comment.mentions,
        created_at=comment.created_at
    )


@app.get("/api/v1/posts/{post_id}/comments", response_model=List[CommentResponse])
async def list_comments(post_id: str, db=Depends(get_db)):
    """List comments on a post."""
    comments = db.query(Comment).filter(Comment.post_id == post_id).order_by(Comment.created_at).all()
    return [CommentResponse(
        id=c.id,
        post_id=c.post_id,
        author_id=c.author_id,
        author_name=c.author.name,
        parent_id=c.parent_id,
        content=c.content,
        mentions=c.mentions,
        created_at=c.created_at
    ) for c in comments]


# --- Routes: Webhooks ---

@app.post("/api/v1/projects/{project_id}/webhooks", response_model=WebhookResponse)
async def create_webhook(
    project_id: str,
    data: WebhookCreate,
    agent: Agent = Depends(require_agent),
    db=Depends(get_db)
):
    """Create a webhook for a project."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "Project not found")
    
    webhook = Webhook(project_id=project_id, url=data.url)
    webhook.events = data.events
    db.add(webhook)
    db.commit()
    db.refresh(webhook)
    
    return WebhookResponse(
        id=webhook.id,
        project_id=webhook.project_id,
        url=webhook.url,
        events=webhook.events,
        active=webhook.active
    )


@app.get("/api/v1/projects/{project_id}/webhooks", response_model=List[WebhookResponse])
async def list_webhooks(project_id: str, agent: Agent = Depends(require_agent), db=Depends(get_db)):
    """List webhooks for a project."""
    webhooks = db.query(Webhook).filter(Webhook.project_id == project_id).all()
    return [WebhookResponse(
        id=w.id,
        project_id=w.project_id,
        url=w.url,
        events=w.events,
        active=w.active
    ) for w in webhooks]


@app.delete("/api/v1/webhooks/{webhook_id}")
async def delete_webhook(webhook_id: str, agent: Agent = Depends(require_agent), db=Depends(get_db)):
    """Delete a webhook."""
    webhook = db.query(Webhook).filter(Webhook.id == webhook_id).first()
    if not webhook:
        raise HTTPException(404, "Webhook not found")
    db.delete(webhook)
    db.commit()
    return {"status": "deleted"}


# --- Routes: Notifications ---

@app.get("/api/v1/notifications", response_model=List[NotificationResponse])
async def list_notifications(
    unread_only: bool = False,
    agent: Agent = Depends(require_agent),
    db=Depends(get_db)
):
    """List notifications for current agent."""
    query = db.query(Notification).filter(Notification.agent_id == agent.id)
    if unread_only:
        query = query.filter(Notification.read == False)
    notifications = query.order_by(Notification.created_at.desc()).limit(50).all()
    return [NotificationResponse(
        id=n.id,
        type=n.type,
        payload=n.payload,
        read=n.read,
        created_at=n.created_at
    ) for n in notifications]


@app.post("/api/v1/notifications/{notification_id}/read")
async def mark_read(notification_id: str, agent: Agent = Depends(require_agent), db=Depends(get_db)):
    """Mark a notification as read."""
    notif = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.agent_id == agent.id
    ).first()
    if not notif:
        raise HTTPException(404, "Notification not found")
    notif.read = True
    db.commit()
    return {"status": "read"}


@app.post("/api/v1/notifications/read-all")
async def mark_all_read(agent: Agent = Depends(require_agent), db=Depends(get_db)):
    """Mark all notifications as read."""
    db.query(Notification).filter(
        Notification.agent_id == agent.id,
        Notification.read == False
    ).update({Notification.read: True})
    db.commit()
    return {"status": "all read"}


# --- Run ---

if __name__ == "__main__":
    import uvicorn
    port = config.get("port", 8080)
    uvicorn.run(app, host="0.0.0.0", port=port)
