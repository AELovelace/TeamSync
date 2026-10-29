-- Device tokens carry a scope. 'pusher' may only check hashes and upload files (the
-- workstation push script); 'admin' may call every API route. Existing tokens become
-- pushers, which is what they were minted for.

ALTER TABLE device_tokens ADD COLUMN scope TEXT NOT NULL DEFAULT 'pusher';
