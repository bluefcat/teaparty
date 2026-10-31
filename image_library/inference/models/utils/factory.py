"""Inference-only factory for the architectures supported by TEA PARTY.

The module names and model definitions are kept checkpoint-compatible with
ML-Danbooru.  Unsupported training backbones are intentionally not imported.
"""

def create_model(args, load_head=False, colo=False):
    name = args.model_name.lower()
    if name == 'caformer_m36':
        from ..caformer import build_caformer
        model = build_caformer('caformer_m36_384', args)
    elif name == 'tresnet_d':
        from ..tresnet import tresnet_f
        if not args.frelu:
            raise ValueError('tresnet_d requires frelu=True in TEA PARTY')
        model = tresnet_f.TResnetD({'args': args, 'num_classes': args.num_classes})
    else:
        raise ValueError(f'Unsupported architecture: {name}')

    if args.use_ml_decoder:
        from ...ml_decoder import ml_decoder
        model = ml_decoder.add_ml_decoder_head(
            model, num_classes=args.num_classes, num_of_groups=args.num_of_groups,
            decoder_embedding=args.decoder_embedding, zsl=args.zsl,
            use_xformers=args.xformers,
            num_layers_decoder=args.num_layers_decoder,
        )
    return model
