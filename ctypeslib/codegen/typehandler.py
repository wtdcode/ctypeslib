"""clangparser - use clang to get preprocess a source code."""

import clang
from clang.cindex import Index, TranslationUnit
from clang.cindex import CursorKind, TypeKind, TokenKind

import logging

import typedesc
import re

from ctypeslib.codegen import util
from ctypeslib.codegen.util import log_entity
from ctypeslib.codegen.handler import ClangHandler

log = logging.getLogger('typehandler')

## DEBUG
import code 

class TypeHandler(ClangHandler):
    """
    Handles Cursor Kind and transform them into typedesc.
    """
    def __init__(self, parser):
        ClangHandler.__init__(self, parser)
        self.init_fundamental_types()

    def parse_cursor(self, cursor):
        return self.parser.parse_cursor(cursor)

    def parse_cursor_type(self, _cursor_type):
        mth = getattr(self, _cursor_type.kind.name)
        return mth(_cursor_type)

    ##########################################################################
    ##### TypeKind handlers#######
    # TODO 

    def init_fundamental_types(self):
        """Registers all fundamental typekind handlers"""
        for _id in range(1,24):
            setattr(self, TypeKind.from_id(_id).name, 
                          self._handle_fundamental_types)

    def _handle_fundamental_types(self, typ):
        """
        Handles POD types nodes.
        see init_fundamental_types for the registration.
        """
        ctypesname = self.get_ctypes_name(typ.kind)
        if typ.kind == TypeKind.VOID:
            size = align = 1
        else:
            size = typ.get_size()
            align = typ.get_align()
        return typedesc.FundamentalType( ctypesname, size, align )


    """ 
    INVALID
    UNEXPOSED
    NULLPTR
    OVERLOAD
    DEPENDENT
    OBJCID
    OBJCCLASS
    OBJCSEL
    COMPLEX
    BLOCKPOINTER
    LVALUEREFERENCE
    RVALUEREFERENCE
    OBJCINTERFACE
    OBJCOBJECTPOINTER
    FUNCTIONNOPROTO
    FUNCTIONPROTO
    VECTOR
    MEMBERPOINTER
    """

    ## const, restrict and volatile
    ## typedesc.CvQualifiedType(typ, const, volatile)
    # Type has multiple functions for const, volatile, restrict
    # not listed has node in the AST.
    # not very useful in python anyway.
    TYPEDEF = ClangHandler._do_nothing
    ENUM = ClangHandler._do_nothing

    @log_entity
    def POINTER(self, _cursor_type):
        """
        Handles POINTER types.
        """
        if not isinstance(_cursor_type, clang.cindex.Type):
            raise TypeError('Please call POINTER with a cursor.type')
        # we shortcut to canonical typedefs and to pointee canonical defs
        _type = _cursor_type.get_pointee().get_canonical()
        _p_type_name = self.get_unique_name(_type)
        # get pointer size
        size = _cursor_type.get_size() # not size of pointee
        align = _cursor_type.get_align() 
        log.debug("POINTER: size:%d align:%d typ:%s"%(size, align, _type.kind))
        if self.is_fundamental_type(_type):
            p_type = self.parse_cursor_type(_type)
        elif self.is_pointer_type(_type) or self.is_array_type(_type):
            p_type = self.parse_cursor_type(_type)
        elif _type.kind == TypeKind.FUNCTIONPROTO:
            p_type = self.parse_cursor_type(_type)
        else: #elif _type.kind == TypeKind.RECORD:
            # check registration
            decl = _type.get_declaration()
            decl_name = self.get_unique_name(decl)
            # Type is already defined OR will be defined later.
            if self.is_registered(decl_name):
                p_type = self.get_registered(decl_name)
            else: # forward declaration, without looping
                log.debug('POINTER: %s type was not previously declared'%(decl_name))
                #code.interact(local=locals())
                p_type = self.parse_cursor(decl)
        #elif _type.kind == TypeKind.FUNCTIONPROTO:
        #    log.error('TypeKind.FUNCTIONPROTO not implemented')
        #    return None
        log.debug("POINTER: pointee type_name:'%s'"%(_p_type_name))
        # return the pointer
        obj = typedesc.PointerType( p_type, size, align)
        obj.location = p_type.location
        return obj

    @log_entity
    def _array_handler(self, _cursor_type):
        """
        Handles all array types. 
        Resolves it's element type and makes a Array typedesc.
        """
        if not isinstance(_cursor_type, clang.cindex.Type):
            raise TypeError('Please call CONSTANTARRAY with a cursor.type')
        # The element type has been previously declared
        # we need to get the canonical typedef, in some cases
        _type = _cursor_type.get_canonical()
        size = _type.get_array_size()
        # FIXME: useful or not ?
        if size == -1 and _type.kind == TypeKind.INCOMPLETEARRAY:
            size = 0
            # Fixes error in negative sized array.
            # FIXME VARIABLEARRAY DEPENDENTSIZEDARRAY
        _array_type = _type.get_array_element_type()#.get_canonical()
        if self.is_fundamental_type(_array_type):
            _subtype = self.parse_cursor_type(_array_type)
        elif self.is_pointer_type(_array_type): 
            #code.interact(local=locals())
            # pointers to POD have no declaration ??
            # FIXME test_struct_with_pointer x_n_t g[1]
            _subtype = self.parse_cursor_type(_array_type)
        else:
            _subtype_decl = _array_type.get_declaration()
            _subtype = self.parse_cursor(_subtype_decl)
            #if _subtype_decl.kind == CursorKind.NO_DECL_FOUND:
            #    pass
            #_subtype_name = self.get_unique_name(_subtype_decl)
            #_subtype = self.get_registered(_subtype_name)
        #code.interact(local=locals())
        obj = typedesc.ArrayType(_subtype, size)
        obj.location = _subtype.location
        return obj

    CONSTANTARRAY = _array_handler
    INCOMPLETEARRAY = _array_handler
    VARIABLEARRAY = _array_handler
    DEPENDENTSIZEDARRAY = _array_handler

    @log_entity
    def FUNCTIONPROTO(self, _cursor_type):
        """Handles function prototype."""
        if not isinstance(_cursor_type, clang.cindex.Type):
            raise TypError('Please call FUNCTIONPROTO with a _cursor_type')
        # id, returns, attributes
        returns = _cursor_type.get_result()
        if self.is_fundamental_type(returns):
            returns = self.parse_cursor_type(returns)
        attributes = []
        obj = typedesc.FunctionType(returns, attributes)
        for i, _attr_type in enumerate(_cursor_type.argument_types()):
            arg = typedesc.Argument("a%d"%(i), self.parse_cursor(_attr_type))
            obj.add_argument( arg )
        #log.debug('FUNCTIONPROTO: can I get args ?')
        #code.interact(local=locals())    
        self.set_location(obj, None)
        return obj

    # structures, unions, classes
    
    @log_entity
    def RECORD(self, _cursor_type):
        ''' A record is a NOT a declaration. A record is the occurrence of of
        previously defined record type. So no action is needed. Type is already 
        known.
        Type is accessible by cursor.type.get_declaration() 
        '''
        if not isinstance(_cursor_type, clang.cindex.Type):
            raise TypeError('Please call RECORD with a cursor.type')
        _decl = _cursor_type.get_declaration() # is a record
        #code.interact(local=locals())
        #_decl_cursor = list(_decl.get_children())[0] # record -> decl
        name = self.get_unique_name(_decl)#_cursor)
        if self.is_registered(name):
            obj = self.get_registered(name)
        else:
            log.warning('Was in RECORD but had to parse record declaration ')
            obj = self.parse_cursor(_decl)
        return obj


    
    
    
