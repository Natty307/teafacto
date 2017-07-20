import pickle, re, numpy as np
from teafacto.util import argprun, ticktock, StringMatrix
from IPython import embed
from teafacto.blocks.word.wordvec import WordEmb, Glove
from teafacto.core.base import Val, asblock
from teafacto.blocks.basic import SMO
from teafacto.blocks.seq import RNNSeqEncoder


def loaddata(p="webqa.data.loaded.pkl"):
    tt = ticktock("loader")
    # region 1. load data
    tt.tick("loading data")
    data = pickle.load(open(p))
    textsm, formsm, validnexttokenses, exampleids, t_ub_ents, splitid = \
        [data[k] for k in "textsm formsm validnexttokenses exampleids t_ub_ents traintestsplit".split()]
    tt.tock("data loaded")
    # endregion
    # region 2. split train test
    train_nl_mat, test_nl_mat = textsm.matrix[:splitid], textsm.matrix[splitid:]
    train_fl_mat, test_fl_mat = formsm.matrix[:splitid], formsm.matrix[splitid:]
    train_vts, test_vts = validnexttokenses[:splitid], validnexttokenses[splitid:]
    nl_dic = textsm._dictionary
    fl_dic = formsm._dictionary
    #embed()
    # endregion
    # region 3. test missing stats
    tt.tick("missing stats")
    test_allrels = set()
    for test_vt in test_vts:
        for test_vte in test_vt:
            test_allrels.update(set([x for x in test_vte if x[0] == ":"]))
    train_allrels = set()
    for train_vt in train_vts:
        for train_vte in train_vt:
            train_allrels.update(set([x for x in train_vte if x[0] == ":"]))
    tt.msg("{}/{} rels in test not in train"
           .format(len(test_allrels.difference(train_allrels)), len(test_allrels)))
    tt.tock("missing stats computed")
    # endregion
    # region 4. relation representations preparing
    # update fl_dic
    allrels = train_allrels | test_allrels
    i = max(fl_dic.values()) + 1
    for rel in allrels:
        if rel not in fl_dic:
            fl_dic[rel] = i
            i += 1
    vtn_mat = np.zeros((len(validnexttokenses), formsm.matrix.shape[1], max(fl_dic.values()) + 1), dtype="int8")
    vtn_mat[:, :, fl_dic["<MASK>"]] = 1
    for i, validnexttokens in enumerate(validnexttokenses):
        for j, validnexttokense in enumerate(validnexttokens[1:]):
            vtn_mat[i, j, fl_dic["<MASK>"]] = 0
            for validnexttoken in validnexttokense:
                if validnexttoken not in fl_dic:
                    pass #print i, j, validnexttoken
                else:
                    vtn_mat[i, j, fl_dic[validnexttoken]] = 1
    train_vtn_mat = vtn_mat[:splitid]
    test_vtn_mat = vtn_mat[splitid:]
    # get list of all relations and make reldic and relmat
    allrels = set([x for x in fl_dic.keys() if x[0] == ":"])
    rel_dic = {}

    def rel_tokenizer(uri):
        pre = ":forward:"
        if uri[:8] == ":reverse":
            pre = ":reverse:"
            uri = uri[8:]
        uri = uri[1:]
        uri = uri.replace(".", " :dot: ").replace("_", " ").split()
        uri = [pre] + uri
        return uri

    sm = StringMatrix(freqcutoff=0)
    sm.tokenize = rel_tokenizer
    for k, rel in enumerate(allrels):
        rel_dic[rel] = k
        sm.add(rel)
    sm.finalize()
    rel_mat = sm.matrix
    rel_mat_dic = sm._dictionary
    # endregion
    return (train_nl_mat, train_fl_mat, train_vtn_mat), (test_nl_mat, test_fl_mat, test_vtn_mat), \
           (nl_dic, fl_dic, rel_dic), (rel_mat, rel_mat_dic)


def run(p="webqa.data.loaded.pkl",
        worddim=50,
        fldim=50,
        encdim=50,
        dropout=0.1,
        testpred=False,
        lr=0.1,
        epochs=20,
        numbats=100,
        ):
    tt = ticktock("script")
    (train_nl_mat, train_fl_mat, train_vtn), (test_nl_mat, test_fl_mat, test_vtn), \
    (nl_dic, fl_dic, rel_dic), (rel_mat, rel_mat_dic) = loaddata(p)

    glove = Glove(worddim)

    tt.tick("building nl reps")
    nl_emb = get_nl_emb(nl_dic, glove=glove, dim=worddim, dropout=dropout)
    tt.tock("built nl reps")

    tt.tick("building fl reps")
    fl_emb = get_fl_emb(fl_dic, rel_dic, rel_mat, rel_mat_dic,
                        glove=glove, dim=fldim, worddim=worddim, dropout=dropout)
    fl_smo = get_fl_smo_from_emb(fl_emb, dropout=dropout)
    tt.tock("built fl reps")

    test_rel_smo(train_nl_mat, train_fl_mat, test_nl_mat, test_fl_mat,
                 nl_emb, fl_dic, fl_smo, encdim=encdim, dropout=dropout,
                 testpred=testpred, numbats=numbats, epochs=epochs, lr=lr)

    # TODO: test training of test_rel_smo

    # TODO: REAL THING
    #       (1) make smo that accepts time-dynamic mask,
    #       (2) put it on top of EncDec without smo
    #               --> don't need to change EncDec (but this only works if teacher forcing)
    embed()


def get_nl_emb(nl_dic, glove=None, dim=50, dropout=None):
    emb = WordEmb(worddic=nl_dic, dim=dim)
    if glove is not None:
        emb = emb.override(glove)
    return emb


def get_fl_emb(fl_dic, rel_dic, rel_mat, rel_mat_dic,
               dim=50, worddim=50, dropout=None, glove=None):
    # !!! fl_dic and rel_dic have different indices for same rel
    # 1. make normal vector reps from fl_dic
    base_emb = WordEmb(worddic=fl_dic, dim=dim)
    # 2. build overriding block
    # 2.1 emb words in rel uris
    relwordemb = WordEmb(worddic=rel_mat_dic, dim=worddim)
    if glove is not None:   # override with glove
        relwordemb = relwordemb.override(glove)
    # 2.2 encode rel mat
    relenc = RNNSeqEncoder.fluent().setembedder(relwordemb)\
        .addlayers(dim=dim, dropout_in=dropout, zoneout=dropout).make()
    rel_mat_val = Val(rel_mat)
    rel_mat_enc = relenc(rel_mat_val)
    # 2.3 use encs in overriding wordemb
    rel_emb = WordEmb(worddic=rel_dic, dim=dim, value=rel_mat_enc)
    # 3. override
    fl_emb = base_emb.override(rel_emb)
    return fl_emb


def get_fl_smo_from_emb(fl_emb, dropout=None):
    smo = SMO(fl_emb.indim, fl_emb.outdim, dropout=dropout, nobias=True)
    smo.l.W = fl_emb.W.T
    return smo


def test_rel_smo(train_nl_mat, train_fl_mat, test_nl_mat, test_fl_mat,
                 nl_emb, fl_dic, fl_smo, encdim=50, dropout=None,
                 testpred=False, numbats=None, epochs=None, lr=None):
    # learn to predict one relation from sentence
    rev_fl_dic = {v: k for k, v in fl_dic.items()}
    # region get gold
    traingold = np.zeros((train_fl_mat.shape[0],), dtype="int32")
    testgold = np.zeros((test_fl_mat.shape[0],), dtype="int32")
    def _get_gold(fl_mat, gold):
        for i in range(len(fl_mat)):
            for j in range(fl_mat.shape[1]):
                if rev_fl_dic[fl_mat[i, j]][0] == ":":
                    gold[i] = fl_mat[i, j]
                    break
    _get_gold(train_fl_mat, traingold)      # in fl dic domain
    _get_gold(test_fl_mat, testgold)
    # endregion

    # encode question
    encoder = RNNSeqEncoder.fluent().setembedder(nl_emb)\
        .addlayers(dim=encdim, bidir=True, dropout_in=dropout, zoneout=dropout)\
        .addlayers(dim=encdim, dropout_in=dropout, zoneout=dropout)\
        .make()

    def block_apply(x):     # (batsize, seqlen)^wordids
        enco = encoder(x)   # (batsize, encdim)
        out = fl_smo(enco)  # (batsize, fl_dic_size)
        return out

    m = asblock(block_apply)

    if testpred:
        testpred = m.predict(train_nl_mat[:5])

    m.train([train_nl_mat], traingold).cross_entropy().accuracy()\
        .adadelta(lr=lr).grad_total_norm(5.)\
        .validate_on([test_nl_mat], testgold).cross_entropy().accuracy()\
        .train(numbats=numbats, epochs=epochs)
    embed()

if __name__ == "__main__":
    argprun(run)