## image-0.3.0 (2026-09-17)

### Features

- **packer**: mount the run workspace as a read-only share with an overlay

## image-0.2.1 (2026-09-17)

### Bug Fixes

- **packer**: fail the probe on any virtio device but disk and serial

## image-0.2.0 (2026-09-16)

### Features

- **packer**: base, agent: added native embeded mcp into image

## image-0.1.1 (2026-09-13)

## image-0.1.0 (2026-09-13)

### Features

- **images**: register image urls, agent downloads them itself

### Build

- **packer**: write the image changelog on bump
- **tests**: move test targets to make/tests
- **packer**: build base and agents together in the packer image
- **packer**: add alpine agent images and release workflow
- **repo**: bootstrap tooling and component layout

### Documentation

- **docs**: describe images, qemu runtime, console and packer
