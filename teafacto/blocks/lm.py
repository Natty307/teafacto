from core import *
from core import tensorops as T
from collections import OrderedDict
from util import ticktock as TT


class VectorEmbed(Block):
    def __init__(self, indim=1000, dim=50, value=None, normalize=False, **kw):
        super(VectorEmbed, self).__init__(**kw)
        self.dim = dim
        self.indim = indim
        if value is None:
            self.W = param((indim, dim), lrmul=1., name="embedder").uniform()
        else:
            self.W = Parameter(value, name="embedder")
        if normalize:
            self.W = self.W.normalize(axis=1)
        # assertions
        assert(value.shape == (self.indim, self.dim))

    def apply(self, inptensor):
        return self.W[inptensor, :]


class WordEmb(object): # unknown words are mapped to index 0, their embedding is a zero vector
    def __init__(self, indim, dim):
        self.D = OrderedDict()
        self.tt = TT(self.__class__.__name__)
        self.dim = dim
        self.indim = indim
        self.W = np.zeros((self.indim, self.dim))
        self._block = None

    @property
    def shape(self):
        return self.W.shape

    def load(self, **kw):
        self.tt.tick("loading")
        if "path" not in kw:
            raise Exception("path must be specified")
        else:
            path = kw["path"]
        i = 1
        for line in open(path):
            if i >= self.indim:
                break
            ls = line.split(" ")
            word = ls[0]
            self.D[word] = i
            self.W[i, :] = np.asarray(map(lambda x: float(x), ls[1:]))
            i += 1
        self.tt.tock("loaded")

    def getindex(self, word):
        return self.D[word] if word in self.D else 0

    def __mul__(self, other):
        return self.getindex(other)

    def __mod__(self, other):
        if isinstance(other, (tuple, list)): # distance
            assert len(other) > 1
            if len(other) == 2:
                return self.getdistance(other[0], other[1])
            else:
                y = other[0]
                return map(lambda x: self.getdistance(y, x), other[1:])
        else:   # embed
            return self.__getitem__(other)

    def getvector(self, word):
        try:
            if isstring(word):
                return self.W[self.D[word]]
            elif isnumber(word):
                return self.W[word, :]
        except Exception:
            return None

    def getdistance(self, A, B, distance=None):
        if distance is None:
            distance = self.cosine
        return distance(self.getvector(A), self.getvector(B))

    def cosine(self, A, B):
        return np.dot(A, B) / (np.linalg.norm(A) * np.linalg.norm(B))

    def __call__(self, A, B):
        return self.getdistance(A, B)

    def __getitem__(self, word):
        v = self.getvector(word)
        return v if v is not None else self.W[0, :]

    @property
    def block(self):
        if self._block is None:
            self._block = self._getblock()
        return self._block

    def _getblock(self):
        return VectorEmbed(indim=self.indim, dim=self.dim, value=self.W, name=self.__class__.__name__)


class Glove(WordEmb):

    def __init__(self, vocabsize, dim, path=None):
        super(Glove, self).__init__(vocabsize, dim)
        self.path = path
        self.load()

    def load(self):
        path = os.path.join(os.path.dirname(__file__), "../../data/glove/glove.6B.%dd.txt" % self.dim)
        super(Glove, self).load(path=path)
