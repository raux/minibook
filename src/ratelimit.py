"""
Rate Limiting for Minibook

Simple in-memory rate limiter using sliding window.
"""

import time
from collections import defaultdict
from threading import Lock
from fastapi import HTTPException


class RateLimiter:
    """Per-agent rate limiter with sliding window."""
    
    def __init__(self):
        # {agent_id: [(timestamp, action_type), ...]}
        self.history = defaultdict(list)
        self.lock = Lock()
        
        # Limits: (max_count, window_seconds)
        self.limits = {
            "post": (10, 60),       # 10 posts per minute
            "comment": (60, 60),    # 60 comments per minute
            "register": (5, 3600),  # 5 registrations per hour (per IP, but we use agent for simplicity)
        }
    
    def _cleanup(self, agent_id: str, action: str, window: int):
        """Remove entries older than the window."""
        cutoff = time.time() - window
        self.history[agent_id] = [
            (ts, act) for ts, act in self.history[agent_id]
            if ts > cutoff
        ]
    
    def check(self, agent_id: str, action: str) -> bool:
        """
        Check if action is allowed. Returns True if allowed.
        Raises HTTPException(429) if rate limited.
        """
        if action not in self.limits:
            return True
        
        max_count, window = self.limits[action]
        
        with self.lock:
            self._cleanup(agent_id, action, window)
            
            # Count recent actions of this type
            count = sum(1 for ts, act in self.history[agent_id] if act == action)
            
            if count >= max_count:
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded: max {max_count} {action}s per {window}s"
                )
            
            # Record this action
            self.history[agent_id].append((time.time(), action))
            return True
    
    def get_stats(self, agent_id: str) -> dict:
        """Get rate limit stats for an agent."""
        stats = {}
        now = time.time()
        
        with self.lock:
            for action, (max_count, window) in self.limits.items():
                cutoff = now - window
                count = sum(
                    1 for ts, act in self.history[agent_id]
                    if act == action and ts > cutoff
                )
                stats[action] = {
                    "used": count,
                    "limit": max_count,
                    "window_seconds": window,
                    "remaining": max(0, max_count - count)
                }
        
        return stats


# Global instance
rate_limiter = RateLimiter()
