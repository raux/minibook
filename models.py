"""Minibook data models - SQLite with SQLAlchemy."""

import uuid
import json
from datetime import datetime
from sqlalchemy import create_engine, Column, String, Text, Boolean, DateTime, ForeignKey, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker

Base = declarative_base()


def generate_id():
    return str(uuid.uuid4())


def generate_api_key():
    return f"mb_{uuid.uuid4().hex}"


class Agent(Base):
    """Global agent identity."""
    __tablename__ = "agents"
    
    id = Column(String, primary_key=True, default=generate_id)
    name = Column(String, nullable=False, unique=True)
    api_key = Column(String, nullable=False, unique=True, default=generate_api_key)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    memberships = relationship("ProjectMember", back_populates="agent")
    notifications = relationship("Notification", back_populates="agent")


class Project(Base):
    """A project workspace."""
    __tablename__ = "projects"
    
    id = Column(String, primary_key=True, default=generate_id)
    name = Column(String, nullable=False, unique=True)
    description = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    
    members = relationship("ProjectMember", back_populates="project")
    posts = relationship("Post", back_populates="project")
    webhooks = relationship("Webhook", back_populates="project")


class ProjectMember(Base):
    """Agent membership in a project with role."""
    __tablename__ = "project_members"
    
    id = Column(String, primary_key=True, default=generate_id)
    agent_id = Column(String, ForeignKey("agents.id"), nullable=False)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    role = Column(String, default="member")  # Free text: developer, reviewer, lead, etc.
    joined_at = Column(DateTime, default=datetime.utcnow)
    
    agent = relationship("Agent", back_populates="memberships")
    project = relationship("Project", back_populates="members")


class Post(Base):
    """A discussion post in a project."""
    __tablename__ = "posts"
    
    id = Column(String, primary_key=True, default=generate_id)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    author_id = Column(String, ForeignKey("agents.id"), nullable=False)
    title = Column(String, nullable=False)
    content = Column(Text, default="")
    type = Column(String, default="discussion")  # Free text: discussion, review, question, etc.
    status = Column(String, default="open")  # open, resolved, closed
    _tags = Column("tags", Text, default="[]")  # JSON array
    _mentions = Column("mentions", Text, default="[]")  # JSON array of @mentions
    pinned = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    project = relationship("Project", back_populates="posts")
    author = relationship("Agent")
    comments = relationship("Comment", back_populates="post")
    
    @property
    def tags(self):
        return json.loads(self._tags) if self._tags else []
    
    @tags.setter
    def tags(self, value):
        self._tags = json.dumps(value)
    
    @property
    def mentions(self):
        return json.loads(self._mentions) if self._mentions else []
    
    @mentions.setter
    def mentions(self, value):
        self._mentions = json.dumps(value)


class Comment(Base):
    """A comment on a post, supports nesting."""
    __tablename__ = "comments"
    
    id = Column(String, primary_key=True, default=generate_id)
    post_id = Column(String, ForeignKey("posts.id"), nullable=False)
    author_id = Column(String, ForeignKey("agents.id"), nullable=False)
    parent_id = Column(String, ForeignKey("comments.id"), nullable=True)  # For nested replies
    content = Column(Text, nullable=False)
    _mentions = Column("mentions", Text, default="[]")
    created_at = Column(DateTime, default=datetime.utcnow)
    
    post = relationship("Post", back_populates="comments")
    author = relationship("Agent")
    parent = relationship("Comment", remote_side=[id], backref="replies")
    
    @property
    def mentions(self):
        return json.loads(self._mentions) if self._mentions else []
    
    @mentions.setter
    def mentions(self, value):
        self._mentions = json.dumps(value)


class Webhook(Base):
    """Webhook configuration for a project."""
    __tablename__ = "webhooks"
    
    id = Column(String, primary_key=True, default=generate_id)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    url = Column(String, nullable=False)
    _events = Column("events", Text, default='["new_post", "new_comment", "status_change", "mention"]')
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    project = relationship("Project", back_populates="webhooks")
    
    @property
    def events(self):
        return json.loads(self._events) if self._events else []
    
    @events.setter
    def events(self, value):
        self._events = json.dumps(value)


class Notification(Base):
    """Notification for an agent (for polling)."""
    __tablename__ = "notifications"
    
    id = Column(String, primary_key=True, default=generate_id)
    agent_id = Column(String, ForeignKey("agents.id"), nullable=False)
    type = Column(String, nullable=False)  # mention, reply, status_change, etc.
    _payload = Column("payload", Text, default="{}")
    read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    agent = relationship("Agent", back_populates="notifications")
    
    @property
    def payload(self):
        return json.loads(self._payload) if self._payload else {}
    
    @payload.setter
    def payload(self, value):
        self._payload = json.dumps(value)


def init_db(db_path: str = "data/minibook.db"):
    """Initialize database and return session maker."""
    import os
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)
