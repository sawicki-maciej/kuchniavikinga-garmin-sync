# Push Instructions

This repository is ready to be pushed to GitHub. The code has been committed locally.

## Quick Push (if you have write access)

```bash
cd /path/to/this/directory
git push -u origin main
```

## Manual Setup

If the above doesn't work due to permissions:

1. Clone the empty repository:
```bash
git clone https://github.com/sawicki-maciej/kuchniavikinga-garmin-sync.git
cd kuchniavikinga-garmin-sync
```

2. Extract the tarball into the cloned directory:
```bash
tar -xzf /opt/cursor/artifacts/kuchniavikinga-garmin-sync-ready.tar.gz
```

3. Commit and push:
```bash
git add .
git commit -m "Initial commit: Kuchnia Vikinga → Garmin Connect nutrition sync"
git push origin main
```

## What's in this repository

- `kuchniavikinga_garmin_sync/` - Python package (sync service)
- `tests/` - pytest test suite
- `contrib/cloudrun/setup.sh` - GCP deployment script
- `README.md` - Complete documentation
- `Dockerfile` - Container build
- Python dependencies and config files

## Ready to deploy

Once pushed, follow the deployment guide in the infra-management repository.
