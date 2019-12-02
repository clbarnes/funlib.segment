from .impl import find_components
from .replace_values import replace_values
import daisy
import glob
import logging
import numpy as np
import os
import skimage.measure
import tempfile

logger = logging.getLogger(__name__)


def relabel_connected_components(array_in, array_out, block_size, num_workers):
    '''Relabel connected components in an array in parallel.

    Args:

        array_in (``daisy.Array``):

            The array to relabel.

        array_out (``daisy.Array``):

            The array to write to. Should initially be empty (i.e., all zeros).

        block_size (``daisy.Coordinate``):

            The size of the blocks to relabel in, in world units.

        num_workers (``int``):

            The number of workers to use.
    '''

    write_roi = daisy.Roi(
        (0,)*len(block_size),
        block_size)
    read_roi = write_roi.grow(array_in.voxel_size, array_in.voxel_size)
    total_roi = array_in.roi.grow(array_in.voxel_size, array_in.voxel_size)

    num_voxels_in_block = (read_roi/array_in.voxel_size).size()

    with tempfile.TemporaryDirectory() as tmpdir:

        daisy.run_blockwise(
            total_roi,
            read_roi,
            write_roi,
            process_function=lambda b: find_components_in_block(
                array_in,
                array_out,
                num_voxels_in_block,
                b,
                tmpdir),
            num_workers=num_workers,
            fit='shrink')

        nodes, edges = read_cross_block_merges(tmpdir)

    components = find_components(nodes, edges)

    logger.debug("Num nodes: %s", len(nodes))
    logger.debug("Num edges: %s", len(edges))
    logger.debug("Num components: %s", len(components))

    write_roi = daisy.Roi(
        (0,)*len(block_size),
        block_size)
    read_roi = write_roi
    total_roi = array_in.roi

    daisy.run_blockwise(
        total_roi,
        read_roi,
        write_roi,
        process_function=lambda b: relabel_in_block(
            array_out,
            nodes,
            components,
            b),
        num_workers=num_workers,
        fit='shrink')


def find_components_in_block(
        array_in,
        array_out,
        num_voxels_in_block,
        block,
        tmpdir):

    logger.debug("Finding components in block %s", block)

    labels = array_in.to_ndarray(block.read_roi, fill_value=0)
    components = skimage.measure.label(
        labels,
        connectivity=1).astype(labels.dtype)

    logger.debug("Labels:\n%s", labels)
    logger.debug("Components:\n%s", components)

    components += block.block_id * num_voxels_in_block
    components[labels == 0] = 0

    logger.debug(
        "Bumping component IDs by %d",
        block.block_id * num_voxels_in_block)

    logger.debug("Components:\n%s", components)

    array_out[block.write_roi] = components[1:-1, 1:-1, 1:-1]
    neighbors = array_out.to_ndarray(roi=block.read_roi, fill_value=0)

    logger.debug("Neighbors:\n%s", neighbors)

    unique_pairs = []

    for d in range(3):

        slices_neg = tuple(
            slice(None) if dd != d else slice(0, 1)
            for dd in range(3)
        )
        slices_pos = tuple(
            slice(None) if dd != d else slice(-1, None)
            for dd in range(3)
        )

        pairs_neg = np.array([
            components[slices_neg].flatten(),
            neighbors[slices_neg].flatten()])
        pairs_neg = pairs_neg.transpose()

        pairs_pos = np.array([
            components[slices_pos].flatten(),
            neighbors[slices_pos].flatten()])
        pairs_pos = pairs_pos.transpose()

        unique_pairs.append(
            np.unique(
                np.concatenate([pairs_neg, pairs_pos]),
                axis=0))

    unique_pairs = np.concatenate(unique_pairs)
    zero_u = unique_pairs[:, 0] == 0
    zero_v = unique_pairs[:, 1] == 0
    non_zero_filter = np.logical_not(np.logical_or(zero_u, zero_v))

    logger.debug("Matching pairs with neighbors: %s", unique_pairs)

    edges = unique_pairs[non_zero_filter]
    nodes = np.unique(edges)

    logger.debug("Final edges: %s", edges)
    logger.debug("Final nodes: %s", nodes)

    np.savez_compressed(
        os.path.join(tmpdir, 'block_%d.npz' % block.block_id),
        nodes=nodes,
        edges=edges)


def relabel_in_block(array, old_values, new_values, block):

    a = array.to_ndarray(block.write_roi)
    replace_values(a, old_values, new_values, inplace=True)
    array[block.write_roi] = a


def read_cross_block_merges(tmpdir):

    block_files = glob.glob(os.path.join(tmpdir, 'block_*.npz'))

    nodes = []
    edges = []
    for block_file in block_files:
        b = np.load(block_file)
        nodes.append(b['nodes'])
        edges.append(b['edges'])

    return np.concatenate(nodes), np.concatenate(edges)
