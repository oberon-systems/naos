use std::fs::File;
use std::os::unix::fs::FileExt;

use crate::libs::error::AgentError;

pub const ROOT_INODE: u32 = 2;
pub const TRUSTED_INDEX: u8 = 4;

const SUPERBLOCK_OFFSET: u64 = 1024;
const SUPERBLOCK_LEN: usize = 1024;
const MAGIC: u16 = 0xEF53;
const STATE_VALID: u16 = 0x1;
const STATE_ERROR: u16 = 0x2;
const INCOMPAT_FILETYPE: u32 = 0x2;
const INCOMPAT_EXTENTS: u32 = 0x40;
const INCOMPAT_64BIT: u32 = 0x80;
const INCOMPAT_FLEX_BG: u32 = 0x200;
// The seed only changes how checksums are computed, and this reader never verifies one.
const INCOMPAT_CSUM_SEED: u32 = 0x2000;
const SUPPORTED_INCOMPAT: u32 =
    INCOMPAT_FILETYPE | INCOMPAT_EXTENTS | INCOMPAT_64BIT | INCOMPAT_FLEX_BG | INCOMPAT_CSUM_SEED;
const EXTENTS_FL: u32 = 0x80000;
const INLINE_DATA_FL: u32 = 0x1000_0000;
const EXTENT_MAGIC: u16 = 0xF30A;
const EXTENT_MAX_DEPTH: u16 = 5;
const EXTENT_UNWRITTEN: u16 = 32768;
const MAX_EXTENTS: usize = 1 << 16;
const XATTR_MAGIC: u32 = 0xEA02_0000;
const MAX_METADATA_BYTES: u64 = 64 * 1024 * 1024;
const CHUNK: usize = 1024 * 1024;

pub const S_IFMT: u16 = 0o170_000;
pub const S_IFREG: u16 = 0o100_000;
pub const S_IFDIR: u16 = 0o040_000;
pub const S_IFLNK: u16 = 0o120_000;
pub const S_IFCHR: u16 = 0o020_000;

pub fn corrupt(reason: impl std::fmt::Display) -> AgentError {
    AgentError::Runtime(format!("upper disk refused: {reason}"))
}

#[derive(Debug, Clone)]
pub struct Inode {
    pub number: u32,
    pub mode: u16,
    pub links: u16,
    pub size: u64,
    flags: u32,
    block: [u8; 60],
    file_acl: u64,
    extra: Vec<u8>,
}

impl Inode {
    pub fn kind(&self) -> u16 {
        self.mode & S_IFMT
    }

    /// Major and minor of a device node, in either of the two encodings ext4 stores.
    pub fn rdev(&self) -> (u32, u32) {
        let old = le32(&self.block, 0);
        if old != 0 {
            return ((old >> 8) & 0xff, old & 0xff);
        }
        let new = le32(&self.block, 4);
        (
            (new & 0xf_ff00) >> 8,
            (new & 0xff) | ((new >> 12) & 0xf_ff00),
        )
    }
}

#[derive(Debug, Clone, Copy)]
struct Extent {
    logical: u64,
    physical: u64,
    len: u64,
    unwritten: bool,
}

pub struct Xattr {
    pub index: u8,
    pub name: Vec<u8>,
    pub value: Vec<u8>,
}

pub struct Ext4 {
    file: File,
    block_size: u64,
    blocks: u64,
    inodes: u32,
    inodes_per_group: u32,
    inode_size: u64,
    inode_tables: Vec<u64>,
}

impl Ext4 {
    pub fn open(file: File) -> Result<Self, AgentError> {
        let image_len = file.metadata()?.len();
        let mut sb = vec![0u8; SUPERBLOCK_LEN];
        read_exact_at(&file, &mut sb, SUPERBLOCK_OFFSET, image_len)?;
        if le16(&sb, 0x38) != MAGIC {
            return Err(corrupt("no ext4 superblock"));
        }
        let state = le16(&sb, 0x3A);
        if state & STATE_VALID == 0 || state & STATE_ERROR != 0 {
            return Err(corrupt("the filesystem was not unmounted cleanly"));
        }
        let incompat = le32(&sb, 0x60);
        if incompat & !SUPPORTED_INCOMPAT != 0 {
            return Err(corrupt(format!(
                "unsupported features {:#x}",
                incompat & !SUPPORTED_INCOMPAT
            )));
        }
        let log = le32(&sb, 0x18);
        if log > 6 {
            return Err(corrupt("block size out of range"));
        }
        let block_size = 1024u64 << log;
        let is64 = incompat & INCOMPAT_64BIT != 0;
        let mut blocks = u64::from(le32(&sb, 0x4));
        if is64 {
            blocks |= u64::from(le32(&sb, 0x150)) << 32;
        }
        if blocks == 0 || blocks > image_len / block_size {
            return Err(corrupt("block count exceeds the disk"));
        }
        let inodes = le32(&sb, 0x0);
        let first_data_block = u64::from(le32(&sb, 0x14));
        let blocks_per_group = u64::from(le32(&sb, 0x20));
        let inodes_per_group = le32(&sb, 0x28);
        let inode_size = if le32(&sb, 0x4C) == 0 {
            128
        } else {
            u64::from(le16(&sb, 0x58))
        };
        if blocks_per_group == 0
            || blocks_per_group > block_size * 8
            || inodes_per_group == 0
            || u64::from(inodes_per_group) > block_size * 8
            || first_data_block >= blocks
            || !(128..=block_size).contains(&inode_size)
            || !inode_size.is_power_of_two()
        {
            return Err(corrupt("inconsistent superblock geometry"));
        }
        let desc_size = if is64 { u64::from(le16(&sb, 0xFE)) } else { 32 };
        if !(32..=1024).contains(&desc_size) || !desc_size.is_power_of_two() {
            return Err(corrupt("group descriptor size out of range"));
        }
        let groups = (blocks - first_data_block).div_ceil(blocks_per_group);
        if u64::from(inodes) > groups * u64::from(inodes_per_group) {
            return Err(corrupt("inode count exceeds the groups"));
        }
        let table_len = groups * desc_size;
        if table_len > MAX_METADATA_BYTES {
            return Err(corrupt("group descriptor table too large"));
        }
        let mut table = vec![0u8; usize::try_from(table_len).map_err(corrupt)?];
        read_exact_at(
            &file,
            &mut table,
            (first_data_block + 1) * block_size,
            image_len,
        )?;
        let desc = usize::try_from(desc_size).map_err(corrupt)?;
        let inode_tables = table
            .chunks_exact(desc)
            .map(|chunk| {
                let mut block = u64::from(le32(chunk, 0x8));
                if desc >= 64 {
                    block |= u64::from(le32(chunk, 0x28)) << 32;
                }
                block
            })
            .collect();
        Ok(Self {
            file,
            block_size,
            blocks,
            inodes,
            inodes_per_group,
            inode_size,
            inode_tables,
        })
    }

    pub fn image_len(&self) -> u64 {
        self.blocks * self.block_size
    }

    pub fn inode(&self, number: u32) -> Result<Inode, AgentError> {
        if number == 0 || number > self.inodes {
            return Err(corrupt(format!("inode {number} out of range")));
        }
        let index = number - 1;
        let group = usize::try_from(index / self.inodes_per_group).map_err(corrupt)?;
        let table = *self
            .inode_tables
            .get(group)
            .ok_or_else(|| corrupt("inode group out of range"))?;
        let table_blocks =
            (u64::from(self.inodes_per_group) * self.inode_size).div_ceil(self.block_size);
        if table == 0 || table.saturating_add(table_blocks) > self.blocks {
            return Err(corrupt("inode table outside the disk"));
        }
        let offset =
            table * self.block_size + u64::from(index % self.inodes_per_group) * self.inode_size;
        let mut raw = vec![0u8; usize::try_from(self.inode_size).map_err(corrupt)?];
        self.read_at(&mut raw, offset)?;
        let mut block = [0u8; 60];
        block.copy_from_slice(&raw[0x28..0x28 + 60]);
        let extra = if raw.len() > 128 {
            let extra_isize = usize::from(le16(&raw, 0x80));
            if 128 + extra_isize > raw.len() || extra_isize % 2 != 0 {
                return Err(corrupt(format!("inode {number} extra size out of range")));
            }
            raw[128 + extra_isize..].to_vec()
        } else {
            Vec::new()
        };
        Ok(Inode {
            number,
            mode: le16(&raw, 0x0),
            links: le16(&raw, 0x1A),
            size: u64::from(le32(&raw, 0x4)) | (u64::from(le32(&raw, 0x6C)) << 32),
            flags: le32(&raw, 0x20),
            block,
            file_acl: u64::from(le32(&raw, 0x68)) | (u64::from(le16(&raw, 0x76)) << 32),
            extra,
        })
    }

    /// Streams the file's bytes in order, holes and unwritten extents as zeros.
    pub fn read_data(
        &self,
        inode: &Inode,
        mut sink: impl FnMut(&[u8]) -> Result<(), AgentError>,
    ) -> Result<(), AgentError> {
        let extents = self.extents(inode)?;
        let mut position = 0u64;
        let mut buffer = vec![0u8; CHUNK];
        for extent in extents
            .iter()
            .map(|extent| (extent.logical * self.block_size, extent))
            .take_while(|(start, _)| *start < inode.size)
        {
            let (start, extent) = extent;
            zeros(&mut buffer, start - position, &mut sink)?;
            let end = (start + extent.len * self.block_size).min(inode.size);
            let mut at = start;
            while at < end {
                let len = usize::try_from((end - at).min(CHUNK as u64)).map_err(corrupt)?;
                let chunk = &mut buffer[..len];
                if extent.unwritten {
                    chunk.fill(0);
                } else {
                    self.read_at(chunk, extent.physical * self.block_size + (at - start))?;
                }
                sink(chunk)?;
                at += len as u64;
            }
            position = end;
        }
        zeros(&mut buffer, inode.size - position, &mut sink)
    }

    fn read_small(&self, inode: &Inode) -> Result<Vec<u8>, AgentError> {
        if inode.size > MAX_METADATA_BYTES {
            return Err(corrupt(format!("inode {} too large", inode.number)));
        }
        let mut data = Vec::new();
        self.read_data(inode, |chunk| {
            data.extend_from_slice(chunk);
            Ok(())
        })?;
        Ok(data)
    }

    /// Every named entry but `.` and `..`, read block by block so hashed directories parse the same.
    pub fn read_dir(&self, inode: &Inode) -> Result<Vec<(Vec<u8>, u32)>, AgentError> {
        if inode.kind() != S_IFDIR {
            return Err(corrupt(format!(
                "inode {} is not a directory",
                inode.number
            )));
        }
        let data = self.read_small(inode)?;
        let block = usize::try_from(self.block_size).map_err(corrupt)?;
        if data.len() % block != 0 {
            return Err(corrupt(format!(
                "directory {} size is not whole blocks",
                inode.number
            )));
        }
        let mut entries = Vec::new();
        for chunk in data.chunks_exact(block) {
            let mut at = 0;
            while at < block {
                if at + 8 > block {
                    return Err(corrupt("directory entry header past the block"));
                }
                let number = le32(chunk, at);
                let rec_len = usize::from(le16(chunk, at + 4));
                let name_len = usize::from(chunk[at + 6]);
                if rec_len < 8 || rec_len % 4 != 0 || at + rec_len > block || 8 + name_len > rec_len
                {
                    return Err(corrupt("directory entry length out of range"));
                }
                let name = &chunk[at + 8..at + 8 + name_len];
                at += rec_len;
                if number == 0 || name == b"." || name == b".." {
                    continue;
                }
                if name.is_empty() || name.iter().any(|byte| matches!(byte, b'/' | 0)) {
                    return Err(corrupt("directory entry name is not a file name"));
                }
                entries.push((name.to_vec(), number));
            }
        }
        entries.sort();
        if entries.windows(2).any(|pair| pair[0].0 == pair[1].0) {
            return Err(corrupt(format!(
                "directory {} repeats a name",
                inode.number
            )));
        }
        Ok(entries)
    }

    pub fn read_link(&self, inode: &Inode) -> Result<Vec<u8>, AgentError> {
        if inode.flags & INLINE_DATA_FL != 0 {
            return Err(corrupt("inline data is not supported"));
        }
        if inode.flags & EXTENTS_FL == 0 {
            let len = usize::try_from(inode.size).map_err(corrupt)?;
            return inode
                .block
                .get(..len)
                .filter(|_| len < inode.block.len())
                .map(<[u8]>::to_vec)
                .ok_or_else(|| corrupt("symlink target out of range"));
        }
        if inode.size > 4096 {
            return Err(corrupt("symlink target too long"));
        }
        self.read_small(inode)
    }

    pub fn xattrs(&self, inode: &Inode) -> Result<Vec<Xattr>, AgentError> {
        let mut found = Vec::new();
        if inode.extra.len() >= 4 && le32(&inode.extra, 0) == XATTR_MAGIC {
            let region = &inode.extra[4..];
            parse_xattrs(region, 0, region, &mut found)?;
        }
        if inode.file_acl != 0 {
            if inode.file_acl >= self.blocks {
                return Err(corrupt("xattr block outside the disk"));
            }
            let mut block = vec![0u8; usize::try_from(self.block_size).map_err(corrupt)?];
            self.read_at(&mut block, inode.file_acl * self.block_size)?;
            if le32(&block, 0) != XATTR_MAGIC || le32(&block, 8) != 1 {
                return Err(corrupt("xattr block header"));
            }
            parse_xattrs(&block, 32, &block, &mut found)?;
        }
        Ok(found)
    }

    fn extents(&self, inode: &Inode) -> Result<Vec<Extent>, AgentError> {
        if inode.flags & INLINE_DATA_FL != 0 {
            return Err(corrupt("inline data is not supported"));
        }
        if inode.flags & EXTENTS_FL == 0 {
            return Err(corrupt(format!(
                "inode {} does not use extents",
                inode.number
            )));
        }
        let mut extents = Vec::new();
        self.extent_node(&inode.block, None, &mut extents)?;
        let mut next = 0u64;
        for extent in &extents {
            if extent.logical < next {
                return Err(corrupt("extents overlap or are out of order"));
            }
            next = extent.logical + extent.len;
        }
        Ok(extents)
    }

    fn extent_node(
        &self,
        node: &[u8],
        expected_depth: Option<u16>,
        extents: &mut Vec<Extent>,
    ) -> Result<(), AgentError> {
        if le16(node, 0) != EXTENT_MAGIC {
            return Err(corrupt("extent header magic"));
        }
        let entries = usize::from(le16(node, 2));
        let depth = le16(node, 6);
        if depth > EXTENT_MAX_DEPTH
            || expected_depth.is_some_and(|expected| expected != depth)
            || entries > usize::from(le16(node, 4))
            || 12 + entries * 12 > node.len()
        {
            return Err(corrupt("extent header out of range"));
        }
        for entry in node[12..12 + entries * 12].as_chunks::<12>().0 {
            if extents.len() >= MAX_EXTENTS {
                return Err(corrupt("too many extents"));
            }
            if depth == 0 {
                let raw_len = le16(entry, 4);
                let (len, unwritten) = if raw_len > EXTENT_UNWRITTEN {
                    (raw_len - EXTENT_UNWRITTEN, true)
                } else {
                    (raw_len, false)
                };
                let physical = u64::from(le32(entry, 8)) | (u64::from(le16(entry, 6)) << 32);
                if len == 0 || physical.saturating_add(u64::from(len)) > self.blocks {
                    return Err(corrupt("extent outside the disk"));
                }
                extents.push(Extent {
                    logical: u64::from(le32(entry, 0)),
                    physical,
                    len: u64::from(len),
                    unwritten,
                });
            } else {
                let leaf = u64::from(le32(entry, 4)) | (u64::from(le16(entry, 8)) << 32);
                if leaf == 0 || leaf >= self.blocks {
                    return Err(corrupt("extent index outside the disk"));
                }
                // Every visit is counted as an extent, so a tree pointing at one block many times ends.
                extents.push(Extent {
                    logical: 0,
                    physical: 0,
                    len: 0,
                    unwritten: true,
                });
                let mut child = vec![0u8; usize::try_from(self.block_size).map_err(corrupt)?];
                self.read_at(&mut child, leaf * self.block_size)?;
                self.extent_node(&child, Some(depth - 1), extents)?;
            }
        }
        if expected_depth.is_none() {
            extents.retain(|extent| extent.len != 0);
        }
        Ok(())
    }

    fn read_at(&self, buffer: &mut [u8], offset: u64) -> Result<(), AgentError> {
        read_exact_at(&self.file, buffer, offset, self.image_len())
    }
}

fn zeros(
    buffer: &mut [u8],
    mut remaining: u64,
    sink: &mut impl FnMut(&[u8]) -> Result<(), AgentError>,
) -> Result<(), AgentError> {
    while remaining > 0 {
        let len = usize::try_from(remaining.min(buffer.len() as u64)).map_err(corrupt)?;
        buffer[..len].fill(0);
        sink(&buffer[..len])?;
        remaining -= len as u64;
    }
    Ok(())
}

fn parse_xattrs(
    entries: &[u8],
    start: usize,
    values: &[u8],
    found: &mut Vec<Xattr>,
) -> Result<(), AgentError> {
    let mut at = start;
    while at + 4 <= entries.len() && le32(entries, at) != 0 {
        if at + 16 > entries.len() {
            return Err(corrupt("xattr entry past its region"));
        }
        let name_len = usize::from(entries[at]);
        let index = entries[at + 1];
        let value_offset = usize::from(le16(entries, at + 2));
        let value_inode = le32(entries, at + 4);
        let value_size = usize::try_from(le32(entries, at + 8)).map_err(corrupt)?;
        let name = entries
            .get(at + 16..at + 16 + name_len)
            .ok_or_else(|| corrupt("xattr name past its region"))?;
        if value_inode != 0 {
            return Err(corrupt("xattr values in inodes are not supported"));
        }
        let value = values
            .get(value_offset..value_offset.saturating_add(value_size))
            .ok_or_else(|| corrupt("xattr value past its region"))?;
        found.push(Xattr {
            index,
            name: name.to_vec(),
            value: value.to_vec(),
        });
        at += (16 + name_len + 3) & !3;
    }
    Ok(())
}

fn read_exact_at(
    file: &File,
    buffer: &mut [u8],
    offset: u64,
    limit: u64,
) -> Result<(), AgentError> {
    if offset.saturating_add(buffer.len() as u64) > limit {
        return Err(corrupt("read past the end of the disk"));
    }
    file.read_exact_at(buffer, offset)?;
    Ok(())
}

fn le16(bytes: &[u8], at: usize) -> u16 {
    u16::from_le_bytes([bytes[at], bytes[at + 1]])
}

fn le32(bytes: &[u8], at: usize) -> u32 {
    u32::from_le_bytes([bytes[at], bytes[at + 1], bytes[at + 2], bytes[at + 3]])
}
