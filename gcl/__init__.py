"""
GCL -- Generic Configuration Language

See README.md for an explanation of GCL and concepts.
"""

from os import path

import pyparsing as p

from . import functions

__version__ = '0.2.0'

def do(*fns):
  def fg(args):
    for fn in fns:
      args = fn(args)
    return args
  return fg

def doapply(what):
  def fn(args):
    return what(*args)
  return fn

def head(x):
  return x[0]

def second(x):
  return x[1]

def inner(x):
  return x[1:-1]

def mkBool(s):
  return True if s == 'true' else False

def drop(x):
  return []

def find_relative(current_dir, rel_path):
  if rel_path.startswith('/'):
    return rel_path
  else:
    return path.normpath(path.join(current_dir, rel_path))


def loader_with_search_path(search_path):
  """Return a searching loader function.

  The loader will search all directories on the given search path.
  """
  def loader(current_file, rel_path):
    """Default file-based loader."""
    base = path.dirname(current_file)
    target_path = find_relative(base, rel_path)
    if not path.isfile(target_path):
      for search in search_path:
        target_path = path.normpath(path.join(search, rel_path))
        if path.isfile(target_path):
          break

    if not path.isfile(target_path):
      raise IOError('No such file: %r, searched %s' %
                    (current_file, ':'.join([base] + search_path)))

    return load(target_path)
  return loader

# Default loader doesn't have any search path
default_loader = loader_with_search_path([])

# Python 2 and 3 compatible string check
try:
    isinstance("", basestring)
    def is_str(s):
        return isinstance(s, basestring)
except NameError:
    def is_str(s):
        return isinstance(s, str)


#----------------------------------------------------------------------
#  Model
#

class ParseContext(object):
  def __init__(self):
    self.filename = '<from string>'
    self.loader = None

the_context = ParseContext()


class EmptyEnvironment(object):
  def __getitem__(self, key):
    raise LookupError('Unbound variable: %r' % key)

  def __contains__(self, key):
    return False


class SourceLocation(object):
  def __init__(self, string, offset):
    self.string = string
    self.offset = offset


class Environment(object):
  """Binding environment, inherits from another Environment."""

  def __init__(self, values, parent=None):
    self.parent = parent or EmptyEnvironment()
    self.values = values

  def __getitem__(self, key):
    if key in self.values:
      return self.values[key]
    return self.parent[key]

  def __contains__(self, key):
    if key in self.values:
      return True
    return key in self.parent

  def extend(self, d):
    return Environment(d or {}, self)

class Thunk(object):
  def eval(self, env):
    raise NotImplementedError('Whoops')


class Null(Thunk):
  """Null, evaluates to None."""
  def __init__(self):
    pass

  def eval(self, env):
    return None

  def __repr__(self):
    return "null";


class Void(Thunk):
  """A missing value."""
  def __init__(self):
    pass

  def eval(self, env):
    raise ValueError('Unbound value')

  def __repr__(self):
    return '<unbound>'


class Constant(Thunk):
  """A GCL constant expression."""
  def __init__(self, value):
    self.value = value

  def eval(self, env):
    return self.value

  def __repr__(self):
    if type(self.value) == bool:
      return 'true' if self.value else 'false'
    return repr(self.value)


class Var(Thunk):
  """Reference to another value."""
  def __init__(self, name, location):
    self.name = name
    self.location = location

  def eval(self, env):
    return env[self.name]

  def __repr__(self):
    return self.name


def mkVar(s, loc, toks):
  return Var(toks[0], SourceLocation(s, loc))


class List(Thunk):
  """A GCL list."""
  def __init__(self, values):
    self.values = values

  def eval(self, env):
    return [v.eval(env) for v in self.values]

  def __repr__(self):
    return repr(self.values)


class ArgList(Thunk):
  """A paren-separated argument list.

  This is actually a shallow wrapper for Python's list type. We can't use that
  because pyparsing will automatically concatenate lists, which we don't want
  in this case.
  """
  def __init__(self, values):
    self.values = values

  def eval(self, env):
    return [v.eval(env) for v in self.values]

  def __repr__(self):
    return '(%s)' % ', '.join(repr(x) for x in self.values)


class UnboundTuple(Thunk):
  """Unbound tuple.

  When evaluating, the tuple doesn't actually evaluate its children. Instead,
  we return a (lazy) Tuple object that only evaluates the elements when they're
  requested.
  """
  def __init__(self, kv_pairs):
    self.items = dict(kv_pairs)

  def eval(self, env):
    return Tuple(self.items, env)

  def __repr__(self):
    return ('{' +
            '; '.join('%s = %r' % (key, value) for key, value in self.items.items()) +
            '}')


class Tuple(object):
  """Bound tuple, with lazy evaluation.

  Contains real values or Thunks. Thunks will be evaluated upon request, but
  not before.

  The parent_env is the environment in which we do lookups for values that are
  not in this Tuple (the lexically enclosing scope).
  """
  def __init__(self, items, parent_env):
    self.__items = items
    self.__parent_env = parent_env

  def dict(self):
    return self.__items

  def get(self, key, default=None):
    if key in self:
      return self[key]
    return default

  def __getitem__(self, key):
    try:
      x = self.get_thunk(key)

      # Check if this is a Thunk that needs to be lazily evaluated before we
      # return it.
      if isinstance(x, Thunk):
        return x.eval(self.env())

      return x
    except Exception as e:
      raise LookupError("Can't get value for %r: %s" % (key, e))

  def __contains__(self, key):
    return key in self.__items

  def env(self):
    return Environment(self, self.__parent_env)

  def keys(self):
    return self.__items.keys()

  def items(self):
    return [(k, self[k]) for k in self.keys()]

  def is_void(self, k):
    return k in self and isinstance(self.get_thunk(k), Void)

  def get_thunk(self, k):
    return self.__items[k]

  def __call__(self, that):
    return CompositeTuple(self, that)

  def _render(self, key):
    if key in self:
      return '%s = %r' % (key, self.get_thunk(key))
    else:
      return '%s' % key

  def __repr__(self):
    return '{%s}' % '; '.join(self._render(k) for k in self.keys())


class LazyEnv(object):
  def __init__(self, names, then, alt):
    self.names = names
    self.then = then
    self.alt = alt

  def __getitem__(self, key):
    if key in self.names:
      return self.then[key]
    return self.alt[key]


class CompositeTuple(object):
  def __init__(self, left, right):
    self.left = left
    self.left_env = self._mk_env(left, right)
    self.right = right
    self.right_env = self._mk_env(right, left)

  def __contains__(self, key):
    return key in self.right or key in self.left

  def keys(self):
    return list(set(self.left.keys()).union(set(self.right.keys())))

  def items(self):
    return [(k, self[k]) for k in self.keys()]

  def _mk_env(self, tup, alt):
    voids = [k for k in tup.keys() if tup.is_void(k)]
    return LazyEnv(voids, alt, tup.env())

  def is_void(self, k):
    return k in self and isinstance(self.get_thunk(k), Void)

  def env(self):
    # Hah. We don't return anything, and it doesn't seem to matter.
    pass

  def get_thunk(self, key):
    # If right has the value, we get it from right (unless it's a Void),
    # otherwise we get it from left.
    if key in self.right:
      return self.right.get_thunk(key)
    return self.left.get_thunk(key)

  def __getitem__(self, key):
    if key in self.right:
      return self.right.get_thunk(key).eval(self.right_env)
    return self.left.get_thunk(key).eval(self.left_env)

  def __call__(self, that):
    return CompositeTuple(self, that)

  def __repr__(self):
    return '%r %r' % (self.left, self.right)


class Application(Thunk):
  """Function application."""
  def __init__(self, functor, args):
    self.functor = functor
    self.args = args

  def eval(self, env):
    fn = self.functor.eval(env)
    args = self.args.eval(env)

    if isinstance(fn, Tuple):
      # Handle tuple application. We check this here so we can give a nicer
      # error messages, related to source as opposed to runtime values, but
      # we do the actual application itself in the Tuple.
      if not isinstance(args, Tuple):
        raise ValueError('Tuple (%r) must be applied to exactly one other tuple (got %r)' %
                         (self.functor, self.args))

    # Any other callable type
    if not callable(fn):
      raise ValueError('Result of %r (%r) not callable' % (self.functor, fn))

    if isinstance(args, list):
      return fn(*args)
    return fn(args)

  def __repr__(self):
    return '%r(%r)' % (self.functor, self.args)


def mkApplications(atoms):
  """Make a sequence of applications from a list of tokens.

  atoms is a list of atoms, which will be handled left-associatively. E.g:

      ['foo', [], []] == foo()() ==> Application(Application('foo', []), [])
  """
  atoms = list(atoms)
  while len(atoms) > 1:
    atoms[0:2] = [Application(atoms[0], atoms[1])]

  # Nothing left to apply
  return atoms[0]


class UnOp(Thunk):
  def __init__(self, op, right):
    self.op = op
    self.right = right

  def eval(self, env):
    right = self.right.eval(env)
    fn = functions.unary_operators.get(self.op, None)
    if fn is None:
      raise LookupError('Unknown unary operator: %s' % self.op)
    return fn(right)

  def __repr__(self):
    return '%s%r' % (self.op, self.right)


def mkUnOp(tokens):
  return UnOp(tokens[0], tokens[1])


class BinOp(Thunk):
  def __init__(self, left, op, right):
    self.left = left
    self.op = op
    self.right = right

  def eval(self, env):
    left = self.left.eval(env)
    right = self.right.eval(env)

    fn = functions.all_binary_operators.get(self.op, None)
    if fn is None:
      raise LookupError('Unknown operator: %s' % self.op)

    return fn(left, right)

  def __repr__(self):
    return ('%r %s %r' % (self.left, self.op, self.right))


def mkBinOps(tokens):
  tokens = list(tokens)
  while len(tokens) > 1:
    assert(len(tokens) >= 3)
    tokens[0:3] = [BinOp(tokens[0], tokens[1], tokens[2])]
  return tokens[0]


class Deref(Thunk):
  """Dereferencing of a dictionary-like object."""
  def __init__(self, haystack, needle):
    self.haystack = haystack
    self.needle = needle

  def eval(self, env):
    return self.haystack.eval(env)[self.needle]

  def __repr__(self):
    return '%s.%s' % (self.haystack, self.needle)


def mkDerefs(tokens):
  tokens = list(tokens)
  while len(tokens) > 1:
    tokens[0:2] = [Deref(tokens[0], tokens[1])]
  return tokens[0]


class Condition(Thunk):
  def __init__(self, cond, then, else_):
    self.cond = cond
    self.then = then
    self.else_ = else_

  def eval(self, env):
    if self.cond.eval(env):
      return self.then.eval(env)
    else:
      return self.else_.eval(env)

  def __repr__(self):
    return 'if %r then %r else %r' % (self.cond, self.then, self.else_)


class Include(Thunk):
  def __init__(self, file_ref):
    self.file_ref = file_ref
    self.current_file = the_context.filename
    self.loader = the_context.loader

  def eval(self, env):
    file_ref = self.file_ref.eval(env)
    if not is_str(file_ref):
      raise ValueError('Included argument (%r) must be a string, got %r' %
                       (self.file_ref, file_ref))

    return self.loader(self.current_file, file_ref)

  def __repr__(self):
    return 'include %r' % self.file_ref



#----------------------------------------------------------------------
#  Grammar
#

def sym(sym):
  return p.Literal(sym).suppress()


def kw(kw):
  return p.Keyword(kw).suppress()


def listMembers(sep, expr, what):
  return p.Optional(p.delimitedList(expr, sep) +
                    p.Optional(sep).suppress()).setParseAction(
                        lambda ts: what(list(ts)))


def bracketedList(l, r, sep, expr, what):
  """Parse bracketed list.

  Empty list is possible, as is a trailing separator.
  """
  return (sym(l) + listMembers(sep, expr, what) + sym(r)).setParseAction(head)


keywords = ['and', 'or', 'not', 'if', 'then', 'else', 'include']

expression = p.Forward()

comment = '#' + p.restOfLine

identifier = p.Word(p.alphanums + '_')

# Contants
integer = p.Combine(p.Word(p.nums)).setParseAction(do(head, int, Constant))
floating = p.Combine(p.Optional(p.Word(p.nums)) + '.' + p.Word(p.nums)).setParseAction(do(head, float, Constant))
dq_string = p.QuotedString('"', escChar='\\', multiline=True).setParseAction(do(head, Constant))
sq_string = p.QuotedString("'", escChar='\\', multiline=True).setParseAction(do(head, Constant))
boolean = p.Or(['true', 'false']).setParseAction(do(head, mkBool, Constant))
null = p.Keyword('null').setParseAction(Null)

# List
list_ = bracketedList('[', ']', ',', expression, List)

# Tuple
tuple_member = ((identifier + '=' + expression).setParseAction(lambda x: (x[0], x[2]))
               | (identifier + ~p.FollowedBy('=')).setParseAction(lambda x: (x[0], Void())))
tuple_members = listMembers(';', tuple_member, UnboundTuple)
tuple = bracketedList('{', '}', ';', tuple_member, UnboundTuple)

# Variable (can't be any of the keywords, which may have lower matching priority)
variable = ~p.oneOf(' '.join(keywords)) + identifier.copy().setParseAction(mkVar)

# Argument list will live by itself as a atom. Actually, it's a tuple, but we
# don't call it that because we use that term for something else already :)
arg_list = bracketedList('(', ')', ',', expression, ArgList)

parenthesized_expr = (sym('(') + expression + ')').setParseAction(head)

unary_op = (p.oneOf(' '.join(functions.unary_operators.keys())) + expression).setParseAction(mkUnOp)

if_then_else = (kw('if') + expression +
                kw('then') + expression +
                kw('else') + expression).setParseAction(doapply(Condition))

include = (kw('include') + expression).setParseAction(doapply(Include))

atom = (floating
        | integer
        | dq_string
        | sq_string
        | boolean
        | list_
        | tuple
        | null
        | unary_op
        | parenthesized_expr
        | if_then_else
        | include
        | variable
        )

# We have two different forms of function application, so they can have 2
# different precedences. This one: fn(args), which binds stronger than
# dereferencing (fn(args).attr == (fn(args)).attr)
applic1 = (atom + p.ZeroOrMore(arg_list)).setParseAction(mkApplications)

# Dereferencing of an expression (obj.bar)
deref = (applic1 + p.ZeroOrMore(p.Literal('.').suppress() + identifier)).setParseAction(mkDerefs)

# Juxtaposition function application (fn arg), must be 1-arg every time
applic2 = (deref + p.ZeroOrMore(deref)).setParseAction(mkApplications)

# All binary operators at various precedence levels go here:
# This piece of code does the moral equivalent of:
#
#     T = F*F | F/F | F
#     E = T+T | T-T | T
#
# etc.
term = applic2
for op_level in functions.binary_operators:
  operator_syms = ' '.join(op_level.keys())
  term = (term + p.ZeroOrMore(p.oneOf(operator_syms) + term)).setParseAction(mkBinOps)

expression << term

# Two entry points: start at an arbitrary expression, or expect the top-level
# scope to be a tuple.
start = expression.ignore(comment)
start_tuple = tuple_members.ignore(comment)

# Notes:
# 'super' or 'base' of some sort?

#----------------------------------------------------------------------
#  Top-level functions
#

default_env = Environment(functions.builtin_functions)

def reads(s, filename=None, loader=None, implicit_tuple=True):
  """Load but don't evaluate a GCL expression from a string."""
  the_context.filename = filename or '<string>'
  the_context.loader = loader or default_loader
  return (start_tuple if implicit_tuple else start).parseString(s, parseAll=True)[0]


def read(filename, loader=None, implicit_tuple=True):
  """Load but don't evaluate a GCL expression from a file."""
  with file(filename, 'r') as f:
    return reads(f.read(),
                 filename=filename,
                 loader=loader,
                 implicit_tuple=implicit_tuple)


def loads(s, filename=None, loader=None, implicit_tuple=True, env=None):
  """Load and evaluate a GCL expression from a string."""
  ast = reads(s, filename=filename, loader=loader, implicit_tuple=implicit_tuple)
  return ast.eval(env or default_env)


def load(filename, loader=None, implicit_tuple=True, env=None):
  """Load and evaluate a GCL expression from a file."""
  with file(filename, 'r') as f:
    return loads(f.read(),
                 filename=filename,
                 loader=loader,
                 implicit_tuple=implicit_tuple,
                 env=env)
