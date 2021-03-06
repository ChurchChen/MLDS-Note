import os
import random
import torch
from torch import autograd
from solver import SolverBase
from torchvision.utils import save_image
from utils import ensure_dir


class DCGANSolver(SolverBase):
    def __init__(self, model, optimizer, loss, metrics, train_loader, val_loader, config):
        super(DCGANSolver, self).__init__(model, optimizer, loss, metrics, config)
        self.train_loader, self.val_loader = train_loader, val_loader
        self.z_test = torch.randn(64, config.z_size, 1, 1).to(self.device)
        self.image_dir = os.path.join(config.save_dir, 'images')
        random.seed(config.seed)
        torch.manual_seed(config.seed)
        if config.use_cuda and torch.cuda.is_available():
            torch.cuda.manual_seed(config.seed)
        ensure_dir(self.image_dir)
        if config.gan == 'LSGAN':
            self.a, self.b, self.c = config.a, config.b, config.c
        elif config.gan == 'WGAN':
            self.clip = self.config.clip
        if config.visdom:
            self._build_visdom()

    def _train_epoch(self, epoch):
        full_loss = []
        sum_loss_g, n_loss_g = 0, 0
        sum_loss_d, n_loss_d = 0, 0
        if epoch % self.config.save_img_step == 0:
            with torch.no_grad():
                fake_test = self.model.G(self.z_test)
            save_image(fake_test.data, '%s/fake_samples_epoch%03d.png' % (self.image_dir, epoch),
                       nrow=8, normalize=True)
        for idx, x in enumerate(self.train_loader):
            b, x = x.size(0), x.to(self.device)
            z = torch.randn(b, self.config.z_size, 1, 1).to(self.device)

            ########################
            # (1) Update D network #
            ########################
            self.optimizer['D'].zero_grad()
            x_fake = self.model.G(z)
            y_pred_fake = self.model.D(x_fake.detach())
            y_pred_real = self.model.D(x)
            if self.config.gan == 'DCGAN':
                y_real = torch.ones(b).to(self.device)
                y_fake = torch.zeros(b).to(self.device)
                errD = (self.loss(y_pred_fake, y_fake) + self.loss(y_pred_real, y_real)) / 2
            elif self.config.gan == 'LSGAN':
                errD = self.loss(y_pred_fake, self.a) + self.loss(y_pred_real, self.b)
            elif self.config.gan == 'WGAN':
                errD = -torch.mean(y_pred_real) + torch.mean(y_pred_fake)
            elif self.config.gan == 'WGAN-GP':
                u = torch.randn(b, 1, 1, 1).uniform_(0, 1).to(self.device)
                grad_out = torch.ones(b).to(self.device)
                x_both = (x * u + x_fake * (1 - u)).requires_grad_()
                grad, = autograd.grad(self.model.D(x_both), x_both, grad_out)
                grad_penalty = ((torch.sum(grad**2, (1, 2, 3)).sqrt() - 1) ** 2).mean()
                errD = -torch.mean(y_pred_real) + torch.mean(y_pred_fake) + self.config.penalty * grad_penalty
            errD.backward()
            self.optimizer['D'].step()
            if self.config.gan == 'WGAN':
                for p in self.model.D.parameters():
                    p.data.clamp_(-self.clip, self.clip)
            sum_loss_d += errD.item()
            n_loss_d += 1

            ########################
            # (2) Update G network #
            ########################
            if idx % self.config.dis_iter == 0:
                self.optimizer['G'].zero_grad()
                z = torch.randn(*z.size()).to(self.device)
                x_fake = self.model.G(z)
                y_pred_fake = self.model.D(x_fake)
                if self.config.gan == 'DCGAN':
                    errG = self.loss(y_pred_fake, y_real)
                elif self.config.gan == 'LSGAN':
                    errG = self.loss(y_pred_fake, self.c)
                elif self.config.gan == 'WGAN' or 'WGAN-GP':
                    errG = -torch.mean(y_pred_fake)
                errG.backward()
                self.optimizer['G'].step()
                sum_loss_g += errG.item()
                n_loss_g += 1

                if self.config.visdom and self.config.visdom_iter:
                    cnt = n_loss_g - 1 + len(self.train_loader) // self.config.dis_iter * (epoch - 1)
                    self.visual.update_vis_line(cnt, [errG.item(), errD.item()], 'train', 'append')

            full_loss.append({
                'iter': idx,
                'loss_g': errG.item(),
                'loss_d': errD.item()
            })
        log = {
            'loss': (sum_loss_g + sum_loss_d) / (n_loss_g + n_loss_d),
            'loss_g': sum_loss_g / n_loss_g,
            'loss_d': sum_loss_d / n_loss_d,
            'full_loss': full_loss
        }
        return log

    def _build_visdom(self):
        """
        visualization with visdom
        """
        from utils import Visdom
        self.visual = Visdom(1)
        if self.config.visdom_iter:
            self.visual.create_vis_line('iter', 'loss', 'training loss with iter', ['lossG', 'lossD'], 'train')
        else:
            self.visual.create_vis_line('epoch', 'loss', 'training loss with epoch', ['lossG', 'lossD'], 'train')
