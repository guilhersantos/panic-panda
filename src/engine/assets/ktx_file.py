# -*- coding: utf-8 -*-

from . import bytes_to_cstruct, IMAGE_PATH
from vulkan import vk, helpers as hvk
from ctypes import Structure, c_ubyte, c_uint32, sizeof, memmove
from collections import namedtuple
import struct

Ktx10HeaderData = hvk.array(c_ubyte, 12, (0xAB, 0x4B, 0x54, 0x58, 0x20, 0x31, 0x31, 0xBB, 0x0D, 0x0A, 0x1A, 0x0A))

# OPENGL to VULKAN texture format conversion table (not included: textureCompressionETC2 / textureCompressionASTC_LDR )
GL_TO_VK_FORMATS = {

    #
    # VULKAN FEATURES: textureCompressionBC
    #

    # S3TC Compressed Texture Image Formats
    0x83F0: (vk.FORMAT_BC1_RGB_UNORM_BLOCK, vk.FORMAT_BC1_RGB_SRGB_BLOCK),
    0x83F1: (vk.FORMAT_BC1_RGBA_UNORM_BLOCK, vk.FORMAT_BC1_RGBA_SRGB_BLOCK),
    0x83F2: (vk.FORMAT_BC2_UNORM_BLOCK, vk.FORMAT_BC2_SRGB_BLOCK),
    0x83F3: (vk.FORMAT_BC3_UNORM_BLOCK, vk.FORMAT_BC3_SRGB_BLOCK),

    # RGTC Compressed Texture Image Formats
    0x8DBB: vk.FORMAT_BC4_UNORM_BLOCK,
    0x8DBC: vk.FORMAT_BC4_SNORM_BLOCK,
    0x8DBD: vk.FORMAT_BC5_UNORM_BLOCK,
    0x8DBE: vk.FORMAT_BC5_SNORM_BLOCK,

    # BPTC Compressed Texture Image Formats
    0x8E8F: vk.FORMAT_BC6H_UFLOAT_BLOCK,
    0x8E8E: vk.FORMAT_BC6H_SFLOAT_BLOCK,
    0x8E8C: vk.FORMAT_BC7_UNORM_BLOCK,
    0x8E8D: vk.FORMAT_BC7_SRGB_BLOCK,
}


MipmapData = namedtuple('MipmapData', ('offset', 'size', 'width', 'height'))
GpuTexture = namedtuple('GpuTexture', ('image', 'view', 'sampler', 'layout'))


class KtxHeader(Structure):
    """
    The header of a ktx file
    """
    _fields_ = (
        ('id', c_ubyte*12),
        ('endianness', c_uint32),
        ('gl_type', c_uint32),
        ('gl_type_size', c_uint32),
        ('gl_format', c_uint32),
        ('gl_internal_format', c_uint32),
        ('gl_base_internal_format', c_uint32),
        ('pixel_width', c_uint32),
        ('pixel_height', c_uint32),
        ('pixel_depth', c_uint32),
        ('number_of_array_elements', c_uint32),
        ('number_of_faces', c_uint32),
        ('number_of_mipmap_levels', c_uint32),
        ('bytes_of_key_value_data', c_uint32),
    )

    def __repr__(self):
        return repr({n: v for n, v in [(n[0], getattr(self, n[0])) for n in self._fields_]})


class KTXFile(object):
    """
    Warning: This class only implements loading functions for the images formats used in this project.
    Any other usage will most likely not work.

    Texture arrays and cubic texture are not supported and the texture endianess must match the system endianess.
    """

    def __init__(self, fname, header, data):
        self.file_name = fname

        # Main texture data
        self.width = header.pixel_width
        self.height = max(header.pixel_height, 1)
        self.depth = max(header.pixel_depth, 1)
        self.mips_level = max(header.number_of_mipmap_levels, 1)
        self.array_element = max(header.number_of_array_elements, 1)
        self.faces = max(header.number_of_faces, 1)
        self.target = KTXFile.header_target(header)
        self.format = KTXFile.header_format(header)
        self.data = bytearray()

        # Mipmap data
        self.mipmaps = []

        if self.array_element > 1 or self.faces > 1:
            raise NotImplementedError("Texture array and cubic textures are not yet implemented.")
            
        # Load the texture data
        data_offset = local_offset = 0
        mip_extent_width, mip_extent_height = self.width, self.height
        for i in range(self.mips_level):
            mipmap_size = struct.unpack_from("=I", data, data_offset)[0]
            data_offset += 4

            self.data.extend(data[data_offset:data_offset+mipmap_size])
            self.mipmaps.append(MipmapData(local_offset, mipmap_size, mip_extent_width, mip_extent_height))

            mip_extent_width //= 2
            mip_extent_height //= 2
            data_offset += mipmap_size
            local_offset += mipmap_size

    @staticmethod
    def header_target(header):
        """
        Get the target of a ktx texture based on the header data
        Cube & array textures are not implemented

        :param header: The header loaded with `load`
        :return:
        """

        if header.pixel_height == 0:
            return vk.IMAGE_TYPE_1D
        elif header.pixel_depth > 0:
            return vk.IMAGE_TYPE_3D

        return vk.IMAGE_TYPE_2D

    @staticmethod
    def header_format(header):
        """
        Check the format of the texture.

        :param header: The parsed file header
        :return: The vulkan format identifier
        """
        h = header
        is_compressed = h.gl_type == 0 and h.gl_type_size == 1 and h.gl_format == 0
        if not is_compressed:
            raise ValueError("Uncompressed file formats not currently supported")

        formats = GL_TO_VK_FORMATS.get(h.gl_internal_format, (None, None))
        fmt = formats if isinstance(formats, int) else formats[0]   # Dirty workaround until SRGB is implemented

        if fmt is None:
            raise ValueError("The format of this texture is not current supported")

        return fmt

    @staticmethod
    def open(path):
        """
        Load and parse a KTX texture

        :param path: The relative path of the file to load
        :return: A KTXFile texture object
        """
        data = length = None
        with (IMAGE_PATH / path).open('rb') as f:
            data = f.read()
            length = len(data)

        # File size check
        if length < sizeof(KtxHeader):
            msg = "The file {} is valid: length inferior to the ktx header"
            raise IOError(msg.format(path))

        # Header check
        header = bytes_to_cstruct(data, KtxHeader)
        if header.id[::] != Ktx10HeaderData[::]:
            msg = "The file {} is not valid: header do not match the ktx header"
            raise IOError(msg.format(path))

        offset = sizeof(KtxHeader) + header.bytes_of_key_value_data
        texture = KTXFile(path, header, data[offset::])

        return texture