# -*- mode: ruby -*-
# vi: set ft=ruby :

Vagrant.configure(2) do |config|
  config.vm.box = "fedora-26"
  config.vm.box_url = "https://download.fedoraproject.org/pub/fedora/linux/releases/26/CloudImages/x86_64/images/Fedora-Cloud-Base-Vagrant-26-1.5.x86_64.vagrant-libvirt.box"
  config.vm.network "forwarded_port", guest: 80, host: 5000
  config.vm.network "private_network", ip: "192.168.33.10"
  config.vm.synced_folder ".", "/vagrant", type: "sshfs"

  config.vm.provider :libvirt do |domain|
    domain.driver = "kvm"
    domain.memory = 2048
    domain.cpus = 2
  end

  config.vm.provision "ansible" do |ansible|
      ansible.playbook = "aux/vagrant-playbook.yml"
  end

  config.vm.post_up_message = <<-EOF
Provisioning complete.
Koschei frontend should be running at http://localhost:5000/
See also: https://github.com/msimacek/koschei/#development
EOF
end
