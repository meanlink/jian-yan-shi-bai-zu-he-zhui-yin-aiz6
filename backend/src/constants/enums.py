from enum import Enum
class Role(str, Enum):
    admin = 'admin'
    analyst = 'analyst'
    viewer = 'viewer'
    partner = 'partner'
class JobStatus(str, Enum):
    queued = 'queued'
    running = 'running'
    completed = 'completed'
    failed = 'failed'
class ReleaseStatus(str, Enum):
    reserved = 'reserved'
    published = 'published'
    failed = 'failed'
